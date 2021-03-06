#!/usr/bin/env python
from __future__ import print_function
""" Populate a blurb book automatically using a 500px-like algorithm"""
import argparse
import glob
import os
import sys
import math
import uuid
import collections
import json
import tempfile
import shutil
import subprocess
import random
import threading
from datetime import datetime
from PIL import Image
from PIL import ImageFont
from PIL import ImageDraw
import lxml.etree as ET
import extractBlurbFiles
import mergeBlurbFiles
import slideshow

ImageInfo = collections.namedtuple('ImageInfo', ('name', 'src', 'width', 'height', 'modified'))
ScaledImageInfo = collections.namedtuple('ScaledImageInfo', ('image', 'scale', 'x', 'y'))

class PageBuilder(object):
    """ Populate images onto pages. """
    def __init__(self, args, images):
        self.args = args
        self.images = images[::-1] # reverses the list, not in-place
        self.page_height = args.page_height-(args.top+args.bottom)
        self.incomplete = True

    def _row_width(self, row):
        num_deltas = len(row)-1
        row_width = sum(img.image.width*img.scale for img in row)
        return row_width + self.args.xspace*num_deltas

    def _page_height(self, rows):
        num_deltas = len(rows)-1
        page_height = sum(row[0].image.height*row[0].scale for row in rows)
        return page_height + self.args.yspace*num_deltas

    def _calc_y_position(self, rows, first):
        """ Calculate appropriate y origin and spacing. """
        current_page_height = self._page_height(rows)
        y = self.args.top
        yspace = self.args.yspace
        if self.args.yspread and len(rows) > 1 and self.page_height > current_page_height:
            yspace += (self.page_height - current_page_height)/(len(rows)-1)
        elif self.args.ycenter:
            if self.args.overfill:
                # It's complicated. Don't center first page at all. Center others based on just the
                # 'full' images. Last page... who knows
                if not first:
                    if self.page_height > current_page_height:
                        # last page - show last 1/3 of first row
                        y -= self._page_height(rows[0:1])*0.66+self.args.yspace
                    else:
                        y += (self.page_height - self._page_height(rows[1:-1]))/2
                        y -= self._page_height(rows[0:1])+self.args.yspace
            else:
                y += (self.page_height - current_page_height)/2
        return (y, yspace)

    def _generate_page(self, page_width, rows, pageno, first, last):  # pylint: disable=I0011,R0913,W0613
        page = []
        y, yspace = self._calc_y_position(rows, first)
        if self.args.overfill and self._page_height(rows) > self.page_height:
            self._unget_row(rows[-1])
            self._unget_row(rows[-2])
        for row in rows:
            if self.args.mirror and pageno%2 == 0:
                x = self.args.right
            else:
                x = self.args.left
            xspace = self.args.xspace
            row_width = self._row_width(row)
            if self.args.xspread and len(row) > 1:
                xspace += (page_width - row_width)/(len(row)-1)
            elif self.args.xcenter:
                x += (page_width - row_width)/2
            elif self.args.mirror and pageno%2 == 1:
                x += (page_width - row_width)
            for img in row:
                page.append(ScaledImageInfo(image=img.image, scale=img.scale, x=x, y=y))
                x += img.image.width*img.scale + xspace
            y += row[0].image.height*row[0].scale + yspace
        return page

    def _preferred_size(self, width, height, _):
        scaledw = float(width) * self.args.image_height/height
        scaledh = self.args.image_height
        return (scaledw, scaledh)

    def _row_scale(self, row_width, page_width, row):
        whitespace = len(row) * self.args.xspace
        target_width = page_width - whitespace
        return target_width/(row_width-whitespace)

    def _get_row(self, page_width):
        row_width = 0
        row = []
        first_image = None
        last_aspect = 0
        while True:
            image = self.images.pop() if self.images else None
            if image is None:
                break
            if self.args.verbose:
                print ('Placing image %s %s ' % (image, ' '))
            width = image.width
            height = image.height
            is_pano = width >= self.args.pano_threshold*height
            aspect = float(image.width)/image.height
            if not first_image:
                first_image = image
                last_aspect = aspect
            scaledw, scaledh = self._preferred_size(width, height, first_image)
            if self.args.verbose and not self.args.mix_aspect:
                if (aspect/last_aspect < 0.66) and not (is_pano or row_width + scaledw > page_width):
                    print ('Switching aspect ratio %s to %s ' % (first_image,image))
            if is_pano or row_width + scaledw > page_width \
                       or (aspect/last_aspect < 0.66 and not self.args.mix_aspect):
                if self.args.verbose:
                    print ('Row break')
                if row:
                    self.images.append(image)
                    # Required scale is what it takes to turn the current row width
                    # into the desired row width, but ignoring the whitespace which is not scaled
                    if self.args.scale:
                        tscale = self._row_scale(row_width, page_width, row)
                        return [ScaledImageInfo(image=i.image, scale=i.scale*tscale, x=0, y=0)
                                for i in row]
                    else:
                        return row
                else:
                    # Single image is too wide for row or is 'pano' - scale to full width or height as appropriate
                    scaledw = page_width
                    scaledh = height * scaledw/width
                    if (scaledh > self.page_height):
                        scaledh = self.page_height
                        scaledw = width * scaledh/height
            row.append(ScaledImageInfo(image=image, scale=float(scaledh)/height, x=0, y=0))
            row_width += scaledw + self.args.xspace
        # leftover images are left unscaled unless the scale required to fill the row is small
        if row:
            scale = self._row_scale(row_width, page_width, row)
            if scale > 1.2: # and len(row) < 3:
                scale = 1.0
                self.incomplete = True
            else:
                self.incomplete = False
            return [ScaledImageInfo(image=i.image, scale=i.scale*scale, x=0, y=0)
                    for i in row]
        else:
            return None

    def _unget_all_row(self, row):
        if row:
            for i in reversed(row):
                self.images.append(i.image)

    def _unget_row(self, row):
        self._unget_all_row(row)

    def next_page(self, page_width, pageno, first):
        """ Get a page from the image list. """
        height = 0
        last = False
        page_rows = []
        while True:
            next_row = self._get_row(page_width)
            if next_row is None:
                last = True
                break
            row_height = next_row[0].image.height * next_row[0].scale
            if page_rows and row_height + height > self.page_height:
                if self.args.overfill:
                    page_rows.append(next_row) # we want it on the bottom
                else:
                    self._unget_row(next_row)
                break
            if page_rows or not self.args.overfill or first:
                height += row_height + self.args.yspace
            page_rows.append(next_row)
        if height:
            height -= self.args.yspace
            return self._generate_page(page_width, page_rows, pageno, first, last)
        else:
            return None

    def get_pages(self, pagenos):
        """ Return a page for each page number provided."""
        i = 0
        first = True
        while i < len(pagenos):
            double = False
            pageno = pagenos[i]
            i += 1
            if self.args.double and pageno % 2 == 0 and \
                i < len(pagenos) and pagenos[i] == pageno+1:
                if self.args.mirror:
                    page_width = (self.args.page_width-self.args.right)*2
                else:
                    page_width = self.args.page_width*2-(self.args.left+self.args.right)
                i += 1
                double = True
            else:
                page_width = self.args.page_width-(self.args.left+self.args.right)
            if double or not self.args.double_only:
                if (pageno % 2 == 0 and self.args.even_only) or \
                   (pageno % 2 == 1 and self.args.odd_only) or \
                   not (self.args.odd_only or self.args.even_only):
                    populated = self.next_page(page_width, pageno, first)
                    first = False
                    if populated:
                        del pagenos[0:i]
                        i = 0
                        yield (populated, pageno, double)
                    else:
                        break

    def get_page_count(self, pagenos):
        """ Return number of pages that this builder would create, and an indication
            of whether last row is complete.
            Note that this is destructive in that the builder is not usable afterwards. """
        pages = self.get_pages(pagenos[:])
        return (sum(1 for _ in pages), self.incomplete)

def fuzzy_sort_coarse(image, pano_threshold):
    """ A very fuzzy sort by aspect ratio - portrait then square then landscape then pano"""
    if image.width > image.height*pano_threshold:
        return 2
    elif image.width > image.height:
        return 1
    elif image.width < image.height:
        return -1
    else:
        return 0

def fuzzy_sort_fine(image):
    """ A slightly less fuzzy sort by aspect ratio"""
    return round(float(image.width)/image.height)

class SmartPageBuilder(PageBuilder):
    """ Populate images onto pages, trying to do so intelligently.
        Smart algorithm mk 2:
        Sort by aspect ratio
        Generate rows that attempt to scale equally (based on aspect ratio of first image in row)
        (note that average might be better but harder as we don't know what images are in the row)
        Allocate rows to pages in randomish order
        if height remaining is greater than some threshold (based on max row height remaining)
            allocate randomly (or maybe cycle through allocating from short end / tall end / middle)
        else:
            allocate best fit or best pair
    """
    def __init__(self, args, images):
        if args.fuzzy == 'coarse':
            images = sorted(images, key=lambda image: fuzzy_sort_coarse(image, args.pano_threshold), reverse=True) # pylint: disable=I0011,W0108
        elif args.fuzzy == 'fine':
            images = sorted(images, key=lambda image: fuzzy_sort_fine(image), reverse=True) # pylint: disable=I0011,W0108
        else:
            images = sorted(images, key=lambda image: float(image.width)/image.height, reverse=True)
        super(SmartPageBuilder, self).__init__(args, images)
        self.all_rows = None
        self.mode = 0
        self.last_row = None
        self.next_row = []
        self.last_width = 0

    def _generate_page(self, page_width, rows, pageno, first, last):   # pylint: disable=I0011,R0913
        if not self.args.scale:
            # sort the rows to a more pleasing order - wide in the middle,
            # narrowest at the bottom
            carried_rows = []
            if self.args.overfill and not first:
                # The first two rows should be the last two rows of the previous page
                carried_rows.append(rows.pop(0))
                carried_rows.append(rows.pop(0))
            new_rows = []
            rows.sort(key=self._row_width, reverse=True)
            #Keep last row last if it's incomplete
            if last and len(rows) and self._row_width(rows[-1]) < page_width*0.75:
                last_row = rows.pop()
            else:
                last_row = None
            for row in rows:
                if len(new_rows) % 2:
                    new_rows.append(row)
                else:
                    new_rows.insert(0, row)
            if last_row:
                new_rows.append(last_row)
            rows = carried_rows + new_rows
        return super(SmartPageBuilder, self)._generate_page(page_width, rows, pageno, first, last)

    def _preferred_size(self, width, height, image):
        """ What width/height would make this image have area self.image_height^2"""
        aspect = float(image.width)/image.height
        if aspect >= 1:
            scaledh = self.args.image_height/math.sqrt(aspect)
        else:
            scaledh = self.args.image_height
        scaledw = float(width) * scaledh/height
        return (scaledw, scaledh)

    def _get_row(self, page_width):
        if self.all_rows is None:
            self.all_rows = []
            while True:
                next_row = super(SmartPageBuilder, self)._get_row(page_width)
                if next_row is None:
                    break
                self.all_rows.append(next_row)
            if self.all_rows and self._row_width(self.all_rows[-1]) < page_width*0.75:
                self.last_row = self.all_rows.pop()
            self.all_rows.sort(key=lambda row: row[0].image.height * row[0].scale)
        # Pick rows from tall, wide, middle in turn, forcing the last (incomplete)
        # row to remain last
        if self.next_row:
            return self.next_row.pop()
        elif not self.all_rows:
            ret = self.last_row
            self.last_row = None
            return ret
        elif self.mode == 0:
            self.mode = 1
            return self.all_rows.pop()
        elif self.mode == 1:
            self.mode = 2
            return self.all_rows.pop(0)
        else:
            self.mode = 0
            return self.all_rows.pop(len(self.all_rows)/2)

    def _unget_row(self, row):
        self.next_row.append(row)

    def next_page(self, page_width, pageno, first):
        if page_width != self.last_width:
            if self.all_rows:
                for row in self.all_rows:
                    self._unget_all_row(row)
            self._unget_all_row(self.last_row)
            for row in self.next_row:
                self._unget_all_row(row)
            self.all_rows = None
            self.last_row = None
            self.next_row = []
        self.last_width = page_width
        return super(SmartPageBuilder, self).next_page(page_width, pageno, first)

class ExifTool(object):
    """ Read exif data using exiftool.

        Derived from a suggestion at
        http://stackoverflow.com/questions/10075115/call-exiftool-from-a-python-script.
    """
    sentinel = "{ready}\n"

    def __init__(self, executable="exiftool"):
        self.executable = executable
        self.process = None
        self.missing_tags = 0

    def __enter__(self):
        self.process = subprocess.Popen(
            [self.executable, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.missing_tags = 0
        return self

    def  __exit__(self, exc_type, exc_value, traceback):
        self.process.stdin.write("-stay_open\nFalse\n")
        self.process.stdin.flush()

    def execute(self, *args):
        """ Execute an exiftool command. """
        args = args + ("-execute\n",)
        self.process.stdin.write(str.join("\n", args))
        self.process.stdin.flush()
        output = ""
        stdout = self.process.stdout.fileno()
        while not output.endswith(self.sentinel):
            output += os.read(stdout, 4096)
        return output[:-len(self.sentinel)]

    def tags(self, filename):
        """ Return exif data as a dictionary. """
        return json.loads(self.execute("-G", "-j", "-n", filename))[0]

    def get_tag(self, filename, tag):
        """ Return single exif tag. """
        ret = self.tags(filename).get(tag, None)
        if not ret:
            self.missing_tags += 1
        return ret

def find_page(doc, pageno):
    """ Find or create specific page in blurb document. """
    matches = doc.xpath('.//section/page[@number=\'%d\']' % pageno)
    if matches:
        return matches[0]
    section = doc.xpath('.//section')[-1]
    # we need to create the page, but we also need to create any pages between the
    # last page and this one, and ensure that we end on an even page.
    # Spreads are also an issue - if the page we are looking for is not there
    # because the page before was a double-spread, then something has gone wrong and we
    # should fail
    last = last_page(doc)
    if pageno < last:
        raise RuntimeError('Unexpected page number')
    for page in range(last+1, (pageno+1)/2 * 2 + 1):
        ET.SubElement(section, 'page', {'color':'#00000000', 'number': str(page)})
    return find_page(doc, pageno)

def empty_pages(doc):
    """ Returns all empty pages in the original Blurb doc. """
    for page in doc.findall('.//section/page'):
        if not page.findall('container'):
            yield page

def last_page(doc):
    """ Returns last page no in the original Blurb doc. """
    pages = doc.xpath('.//section/page')
    return int(pages[-1].get('number'))

def parse_args():                         # pylint: disable=I0011,R0915
    """ Command line parsing code"""

    class LoadFromJson(argparse.Action):  # pylint: disable=I0011,R0903
        """ argparse action to support reading options from a json dictionary. """
        def __call__(self, parser, namespace, values, option_string=None):
            for key, value in values.iteritems():
                if key != option_string.lstrip('-'):
                    key = key.replace('-', '_')
                    setattr(namespace, key, value)

    def jsonfile(filename):
        """ Argparse helper for parsing json files. """
        try:
            with open(filename) as json_data:
                return json.load(json_data)
        except:
            raise argparse.ArgumentTypeError('Invalid option file %s' % filename)

    def _add_invertable(parser, name, help_string):
        dest = name.replace('-', '_')
        parser.add_argument('--'+name, dest=dest, action='store_true', help=help_string)
        parser.add_argument('--no-'+name, dest=dest, action='store_false', help=argparse.SUPPRESS)

    parser = argparse.ArgumentParser(description='Update blurb files',
                                     fromfile_prefix_chars='@',
                                     add_help=False)
    required = parser.add_argument_group('Required arguments (at least one of...)')
    required.add_argument('--input', help='Input blurb file')
    required.add_argument('--output', help='Output blurb file')
    required.add_argument('--output-dir', help='Output unpacked blurb directory')

    optional = parser.add_argument_group('Optional arguments')
    optional.add_argument('-h', '--height', dest='page_height', metavar='N', type=float, default=-1,
                          help='Maximum height of a page (default: read from blurb doc)')
    optional.add_argument('-w', '--width', dest='page_width', metavar='N', type=float, default=-1,
                          help='Maximum width of a page (default: read from blurb doc)')
    optional.add_argument('--image-height', metavar='N', type=float,
                          default=200.0, help='Minimum height of an image (default: %(default).0f)')
    optional.add_argument('--xspace', dest='xspace', metavar='N', type=float, default=0.0,
                          help='X gap between images (default: %(default).0f)')
    optional.add_argument('--yspace', dest='yspace', metavar='N', type=float, default=0.0,
                          help='Y gap between images (default: %(default).0f)')
    optional.add_argument('--left', dest='left', metavar='N', type=float, default=-1.0,
                          help='Left margin')
    optional.add_argument('--right', dest='right', metavar='N', type=float, default=28.0,
                          help='Right margin')
    optional.add_argument('--top', dest='top', metavar='N', type=float, default=28.0,
                          help='Top margin')
    optional.add_argument('--bottom', dest='bottom', metavar='N', type=float, default=28,
                          help='Bottom margin')
    optional.add_argument('--format', choices=sorted(PRESETS.keys()),
                          default='large-landscape', help='Blurb book layout if creating new book')
    optional.add_argument('--paper', choices=PAPERS,
                          default='standard', help='Blurb paper choice if creating new book')
    optional.add_argument('--import-only', action='store_true',
                          help='Import images only, do not layout')

    layout_flags = parser.add_argument_group('Layout options (also support --no-xxxx to turn off)')
    _add_invertable(layout_flags, 'mirror', 'Mirror left/right margins on even/odd pages')
    _add_invertable(layout_flags, 'smart', 'Smart sort mode')
    _add_invertable(layout_flags, 'ycenter', 'Center rows vertically within page')
    _add_invertable(layout_flags, 'xcenter', 'Center rows horizontally within page')
    _add_invertable(layout_flags, 'xspread', 'Spread images horizontally within rows')
    _add_invertable(layout_flags, 'yspread', 'Spread rows vertically within pages')
    _add_invertable(layout_flags, 'scale', 'Scale images within rows to fill width')
    _add_invertable(layout_flags, 'double', 'Use double-page spreads where possible')
    _add_invertable(layout_flags, 'double-pano', 'Use double-page spreads for pano images')
    _add_invertable(layout_flags, 'double-only', 'Only populate double-page spreads')
    _add_invertable(layout_flags, 'odd-only', 'Only populate odd-numbered pages')
    _add_invertable(layout_flags, 'even-only', 'Only populate even-numbered pages')
    _add_invertable(layout_flags, 'overfill', 'Overfill pages')
    _add_invertable(layout_flags, 'add-pages', 'Add pages to existing book if necessary')
    _add_invertable(layout_flags, 'single', 'Scale all images to fit on single page')
    _add_invertable(layout_flags, 'mix-aspect', 'Allow mixed aspect-ratios on one row')
    layout_flags.add_argument('--scale-pages', type=int, default=0,
                              help='Scale all images to fit on N pages')
    layout_flags.add_argument('--pano-threshold', metavar='N', type=float,
                          default=3.0, help='Threshold to consider an image panoramic (default: %(default).0f)')
    layout_flags.add_argument('--aspect-threshold', metavar='N', type=float,
                          default=0.66, help='Threshold to consider an image panoramic (default: %(default).0f)')

    sort_options = parser.add_argument_group('Sorting options')
    sort_options.add_argument('--random',
                              help='Shuffle images using seed')
    sort_options.add_argument('--sort',
                              help='Sort order')
    sort_options.add_argument('--fuzzy', choices=['coarse', 'fine', 'off'],
                              default='off', help='Use fuzzy sorting in smart flow mode')
    sort_options.add_argument('--reverse', action='store_true',
                              help='Reverse image sort order')
    sort_options.add_argument('--exiftool', default='exiftool',
                              help='Location of exiftool (needed if sorting by exif fields')

    section_options = parser.add_argument_group('Section options')
    section_options.add_argument("--sections", metavar='<section-name>', nargs='+',
                                 help='Names/order of sections to include')
    section_options.add_argument("--section-field", metavar='<exif-tag>',
                                 help='Exif tag used for sections')
    section_options.add_argument("--section-start", choices=['any', 'even', 'odd', 'full'],
                                 default='any', help='Where to start new sections')
    section_options.add_argument("--section-blank-start", type=int,
                                 help='Number of blank pages to leave at start of section')
    section_options.add_argument("--section-blank-end", type=int,
                                 help='Number of blank pages to leave at end of section')
    section_options.add_argument("--section-add-title", action='store_true',
                                 help='Add a title page before this section')
    section_options.add_argument("--section-title",
                                 help='Title for this section')
    section_options.add_argument("--section-title-font", default='arial',
                                 help='Font for this section\'s title')
    section_options.add_argument("--section-title-fontsize", type=int, default=48,
                                 help='Font size for this section\'s title')

    misc_options = parser.add_argument_group('Miscellaneous options')
    misc_options.add_argument('--help', action='help', help='show this help message and exit')
    misc_options.add_argument('-f', '--force', dest='force', help='Force overwrite',
                              action='store_true')
    misc_options.add_argument('--preview', help='Preview pages',
                              action='store_true')
    misc_options.add_argument('--preview-info', help='Output image information in preview',
                              action='store_true')
    misc_options.add_argument('--check-sizes', help='Check image sizes',
                              action='store_true')
    misc_options.add_argument('--config', metavar='<jsonfile>', type=jsonfile, action=LoadFromJson,
                              help='Load options from specified file')
    misc_options.add_argument('--no-default-config', action='store_true',
                              help='Do not try to load default configuration')
    misc_options.add_argument('--max-page', type=int, default=240,
                              help='Maximum page number')
    misc_options.add_argument('--min-ppi', type=int, default=300,
                              help='Minimum PPI')
    misc_options.add_argument('--max-ppi', type=int, default=500,
                              help='Maximum PPI')
    misc_options.add_argument('--normal-ppi', type=int, default=300,
                              help='Target PPI')
    misc_options.add_argument('--preview-ppi', type=int, default=150,
                              help='Preview PPI')
    misc_options.add_argument('--verbose', action='store_true',
                              help='Enable verbose tracing')

    positional_options = parser.add_argument_group('Positional arguments')
    positional_options.add_argument('image_list', metavar='<image-file>', nargs='*',
                                    help='Image(s) to add to blurb book')

    if os.path.exists('./flowblurb.json') and '--no-default-config' not in sys.argv:
        args = parser.parse_args(['--config', './flowblurb.json']+sys.argv[1:])
    else:
        args = parser.parse_args()

    if args.double_only:
        args.double = True
    if args.output and os.path.exists(args.output) and not args.force:
        print ('Target file %s already exists' % args.output)
        sys.exit()
    if not args.output:
        args.output = args.input
    if not (args.output or args.output_dir):
        print ('No target specified')
        sys.exit()
    return args

def update_blurb(doc, populated):
    """ Update a blurb file from a populated layout. """
    for layout, pageno, double in populated:
        page = find_page(doc, pageno)
        page.set('spread', 'true' if double else 'false')
        if double:
            rhs = find_page(doc, pageno+1)
            rhs.getparent().remove(rhs)
        for img in layout:
            container = ET.SubElement(page, 'container', {
                'x': str(img.x),
                'y': str(img.y),
                'width': str(img.image.width*img.scale),
                'height': str(img.image.height*img.scale),
                'type': 'image',
                'transform': '1 0 0 1',
                'id': str(uuid.uuid4()),
            })
            ET.SubElement(container, 'image',
                          {'scale':str(img.scale),
                           'x':'0', 'y':'0',
                           'autolayout':'fill',
                           'flip':'none',
                           'src':img.image.name+'.jpg'
                          })

def preview(args, populated, previews):
    """ Output jpegs for each tiled page. """
    threads = []
    for layout, pageno, double in populated:
        thread = threading.Thread(target=preview_one, args=(layout, pageno, double, args, previews))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    previews.sort()

def preview_one(layout, pageno, double, args, previews):
    """ Output jpegs for a single tiled page. """
    preview_scale = float(args.preview_ppi)/72
    if double:
        page_size = (int(args.page_width*preview_scale*2), int(args.page_height*preview_scale))
    else:
        page_size = (int(args.page_width*preview_scale), int(args.page_height*preview_scale))
    canvas = Image.new('RGB', page_size, (240, 240, 240))
    for img in layout:
        x = int(img.x*preview_scale)
        y = int(img.y*preview_scale)
        width = int(img.image.width*img.scale*preview_scale)
        height = int(img.image.height*img.scale*preview_scale)
        src = args.output_dir + '/images/' + img.image.name+'.jpg'
        in_image = Image.open(src)
        resized = in_image.resize((width, height))
        canvas.paste(resized, (x, y))
        if args.preview_info:
            font = ImageFont.truetype("Arial Bold.ttf", int(16*preview_scale))
            draw = ImageDraw.Draw(canvas)
            ppi = 72.0/img.scale
            name = os.path.splitext(os.path.basename(img.image.src))[0]
            aspect = float(width)/height
            text = '%s\n%d ppi\n%.2f' % (name, ppi, aspect)
            draw.text((x, y), text, (57, 255, 20), font=font)
            if ppi < args.min_ppi:
                draw.line((x, y, x+width, y+height), fill='red', width=5)
                draw.line((x, y+height, x+width, y), fill='red', width=5)
            elif ppi > args.max_ppi:
                draw.line((x, y, x+width, y+height), fill='green', width=5)
                draw.line((x, y+height, x+width, y), fill='green', width=5)
    canvas.save('page%03d.jpg'%pageno)
    previews.append('page%03d.jpg'%pageno)

def recommend_size(args, img):
    """ Recommend size (in pixels) for populated image to reach 300 dpi"""
    current_ppi = 72.0/img.scale
    target_ppi = args.normal_ppi
    scale = target_ppi/current_ppi
    return (int(img.image.width*scale), int(img.image.height*scale))

def check_sizes(args, populated):
    """ Check all image resolutions are reasonable. """
    for layout, _, _ in populated:
        for img in layout:
            ppi = 72.0/img.scale
            name = os.path.splitext(os.path.basename(img.image.src))[0]
            width, height = recommend_size(args, img)
            if ppi < args.min_ppi:
                print ('Resolution too low for %s (%d ppi) - ideal size (%d,%d)' % \
                       (name, ppi, width, height))
            elif ppi > args.max_ppi:
                print ('Resolution too high for %s (%d ppi) - ideal size (%d,%d)' % \
                       (name, ppi, width, height))

def create_backup(extracted):
    """ Create a backup of the bbf2.xml in old_bbfs/bbf2_000000000<n>.xml """
    backup_path = extracted + "/old_bbfs"
    if not os.path.exists(backup_path):
        os.makedirs(backup_path)
    next_file = 1
    while True:
        backup_file = "%s/bbf2_%.10d.xml" % (backup_path, next_file)
        if not os.path.exists(backup_file):
            shutil.copyfile(extracted+"/bbf2.xml", backup_file)
            break
        next_file += 1

def link_or_copy(source, dest):
    """ Symlink a file if we can, else copy it."""
    os_symlink = getattr(os, "symlink", None)
    if callable(os_symlink):
        os_symlink(source, dest)
    else:
        shutil.copyfile(source, dest)

def populate_media(project_dir, media, images):
    """ Add listed images to the media_registry xml. """
    (parent,) = media.xpath('./images')
    changed = False
    for image_name in images:
        image_name = os.path.abspath(image_name)
        if not media.xpath('.//images/media[@src=\'%s\']' % image_name):
            img = Image.open(image_name)
            width, height = img.size
            modified = datetime.fromtimestamp(os.path.getmtime(image_name))
            ext = os.path.splitext(image_name)[1][1:].lower()
            guid = str(uuid.uuid4())
            ET.SubElement(parent, 'media', {
                'src' : image_name,
                'validated': 'true',
                'width' : str(width),
                'height' : str(height),
                'guid': guid,
                'modified': modified.isoformat(),
                'ext': ext,
                'group': ''
            })
            if not os.path.exists("%s/images" % (project_dir)):
                os.makedirs("%s/images" % (project_dir))
            link_or_copy(image_name, "%s/images/%s.%s" % (project_dir, guid, ext))
            # creation of the thumbnails deferred until merge time
            changed = True
    return changed

def load_media(project_dir, image_list):
    """ Load and/or populate blurb media registry. """
    if os.path.isfile(project_dir+'/media_registry.xml'):
        media = ET.parse(project_dir+'/media_registry.xml')
    else:
        root = ET.Element('medialist')
        for elem in ['images', 'video', 'audio', 'text']:
            ET.SubElement(root, elem)
        media = ET.ElementTree(root)
    if image_list and populate_media(project_dir, media, image_list):
        media.write(project_dir+'/media_registry.xml', pretty_print=True)
    return media

def sort_images(images, args):
    """ Sort images according to requested order. """
    if args.random:
        random.seed(args.random)
        random.shuffle(images)
    if args.sort:
        if args.sort == 'name':
            return sorted(images, key=lambda image: image.src)
        elif args.sort == 'date':
            return sorted(images, key=lambda image: image.modified)
        elif args.sort == 'size':
            return sorted(images, key=lambda image: int(image.width)*int(image.height))
        else:
            if args.sort == 'rating':
                args.sort = 'XMP:Rating'
            print ('Sorting by exiftool field %s' % args.sort)
            with ExifTool(executable=args.exiftool) as exiftool:
                images = sorted(images,
                                key=lambda image: exiftool.get_tag(image.src, args.sort))
                if exiftool.missing_tags:
                    print ('Warning: exif tag %s not found in %d of %d images' % \
                          (args.sort, exiftool.missing_tags, len(images)))
                return images
    else:
        return images

PRESETS = {
    'small-square': (495, 495, 'square'),
    'standard-landscape': (693, 594, 'landscape'),
    'standard-portrait': (585, 738, 'true-portrait'),
    'large-landscape': (909, 783, 'large-landscape'),
    'large-square': (855, 864, 'large-square'),
    'magazine': (621, 810, 'letter')
}

PAPERS = ['standard', 'premium_lustre', 'premium_matte', 'pro_medium_gloss', 'pro_uncoated']

def initialize_blurb_directory(path, args):
    """ Create a blank blurb directory."""
    width, height, name = PRESETS.get(args.format)
    if not os.path.exists(path):
        os.makedirs(path)
    with open(path+'/.version', 'w') as version_file:
        version_file.write('4\n')
    project_image = Image.new('RGB', (139, 120), "white") # Size not critical
    project_image.save(path+'/project_image.jpg')
    with open(path+'/project_settings.json', 'w') as settings_file:
        json.dump({"checkoutUrl": "",
                   "coverType": "imagewrap",
                   "enhanceImages": False,
                   "guidelines": {},
                   "paperType": args.paper+'_paper',
                   "recentColors": [],
                   "showGuidelines": False,
                   "uploadGuids": {
                       "bbf2ProjectId": "",
                       "bookId": "",
                       "coverDesignGuids": [],
                       "ebookIdReflowable": ""
                   },
                   "uploadedReflowable": False,
                   "version": "1.0"
                  }, settings_file, indent=4, sort_keys=True)
    book = ET.Element('book', {'schema':'2.8', 'color':'true',
                               'width': str(width), 'height': str(height), 'format': name})
    info = ET.SubElement(book, 'info')
    ET.SubElement(info, 'title')
    ET.SubElement(info, 'isbn').text = 'isbnOff'
    ET.SubElement(info, 'guid').text = str(uuid.uuid4())
    ET.SubElement(info, 'logo').text = 'white'
    ET.SubElement(info, 'source', {'version':'1.1.148', 'versionCreatedWith':'1.1.148',
                                   'branch': 'production'}).text = 'BookWright'
    ET.SubElement(info, 'bookType').text = 'BlurbBook'
    masterpage = ET.SubElement(book, 'masterpage')
    ET.SubElement(masterpage, 'page', {'color': '#ffffff', 'number': '-1'})
    ET.SubElement(masterpage, 'page', {'color': '#ffffff', 'number': '-1'})

    # Seems that BookWright is happy to create the coers that are appropriate
    ebook = ET.SubElement(book, 'cover', {'type': 'ebook'})
    ET.SubElement(ebook, 'coversheet', {'color': '#ffffff',
                                        'width': str(width), 'height': str(height),})

    section = ET.SubElement(book, 'section', {'name':''})
    for pageno in range(0, 20):
        ET.SubElement(section, 'page', {'color':'#00000000', 'number': str(pageno+1)})
    ET.ElementTree(book).write(path+'/bbf2.xml', pretty_print=True)

def initialize_output_directory(args):
    """ Check/create output directories. """
    if args.output_dir:
        if os.path.exists(args.output_dir):
            if not args.force:
                print ('Target directory %s already exists' % args.output_dir)
                sys.exit()
            shutil.rmtree(args.output_dir)
        istemp = False
    else:
        args.output_dir = tempfile.mkdtemp()
        istemp = True

    if args.input:
        extractBlurbFiles.extract(args.input, args.output_dir)
        create_backup(args.output_dir)
    else:
        initialize_blurb_directory(args.output_dir, args)
    return istemp

def find_sections(args, images):
    """ Map images to sections using specified exif field. """
    sections = {}
    with ExifTool(executable=args.exiftool) as exiftool:
        for image in images:
            this_section = exiftool.get_tag(image.src, args.section_field)
            if this_section in sections:
                sections[this_section].append(image)
            else:
                sections[this_section] = [image]
    return sections

def add_title_page(doc, args, pageno):
    """ Add a section title page. """
    page = find_page(doc, pageno)
    title = args.section_title
    container = ET.SubElement(page, 'container', {
        "transform": "1 0 0 1", "type": "text",
        "x": str(args.right if pageno%2 == 0 else args.left),
        "y": str(args.top),
        "width": str(args.page_width-(args.left+args.right)),
        "height": str(args.page_height-(args.top+args.bottom))
        })
    text = ET.SubElement(container, 'text', {'valign': 'middle'})
    text.text = '<p class="align-center line-height-qt">' \
                 '<span class="font-%s" ' \
                 'style="font-size:%dpx;color:#000000;">%s</span></p>' % \
                 (args.section_title_font, args.section_title_fontsize, title)

def make_builder(args, images):
    """ Return a PageBuilder object appropriate for given args."""
    if args.smart:
        return SmartPageBuilder(args, images)
    else:
        if args.reverse:
            images = images[::-1]
        return PageBuilder(args, images)

def populate_pages(args, images, pagenos):
    """ Return a page for each page number provided."""
    return make_builder(args, images).get_pages(pagenos)

def populate_single_page(args, count, images, pagenos):
    """ Return a generator that scales all supplied images to fit on fixed number of pages."""
    def _try_it():
        builder = make_builder(args, images)
        page_count, incomplete = builder.get_page_count(pagenos)
        return (page_count, incomplete)

    while True:
        page_count, incomplete = _try_it()
        if not page_count:
            break
        if page_count > count:
            while page_count > count:
                args.image_height *= 0.99
                page_count, incomplete = _try_it()
        else:
            while page_count < count:
                args.image_height *= 1.01
                page_count, incomplete = _try_it()
            args.image_height /= 1.01
        best_height = args.image_height
        perfect_height = 0
        while page_count <= count:
            args.image_height += 1
            page_count, incomplete = _try_it()
            if page_count <= count:
                best_height = args.image_height
            if page_count == count and not incomplete:
                perfect_height = args.image_height
        if perfect_height:
            args.image_height = perfect_height
            incomplete = False
        else:
            args.image_height = best_height
        if not incomplete:
            break
        if args.smart and args.random:
            args.random = args.random[:-1]
        else:
            break
    print ('Optimized image height is %d' % (args.image_height))
    if args.random:
        print ('Optimized seed %s' % (args.random))
    print ('Optimization incomplete: %d' % (incomplete))
    return populate_pages(args, images, pagenos)

def populate_section(doc, args, pagenos, images, previews):
    """ Populate all images for a single section. """
    images = sort_images(images, args)
    if args.section_start == 'even' or args.section_start == 'full':
        while pagenos and (pagenos[0] % 2 == 1):
            pagenos.pop(0)
    if args.section_start == 'odd' or args.section_start == 'full':
        while pagenos and (pagenos[0] % 2 == 0):
            pagenos.pop(0)
    if args.section_add_title:
        add_title_page(doc, args, pagenos.pop(0))
    if args.section_blank_start:
        del pagenos[0:args.section_blank_start]
    if args.scale_pages:
        local_args = argparse.Namespace(**vars(args))
        populated = list(populate_single_page(local_args, args.scale_pages, images, pagenos))
    else:
        populated = list(populate_pages(args, images, pagenos))
    if args.check_sizes:
        check_sizes(args, populated)
    if args.preview:
        preview(args, populated, previews)
    else:
        update_blurb(doc, populated)
    if args.section_blank_end:
        del pagenos[0:args.section_blank_end]

def populate_sections(doc, args, pagenos, images, previews):
    """ Populate all images for multiple sections. """
    sections_map = find_sections(args, images)
    if not args.sections:
        args.sections = sorted(sections_map.keys())
    for section in args.sections:
        section_args = argparse.Namespace(**vars(args))
        setattr(section_args, 'section_title', section)
        if os.path.isfile(section+'.json'):
            with open(section+'.json') as json_data:
                new_args = json.load(json_data)
                for key, value in new_args.iteritems():
                    key = key.replace('-', '_')
                    setattr(section_args, key, value)
        populate_section(doc, section_args, pagenos, sections_map.get(section, []), previews)

def main():
    """ Main code. """
    args = parse_args()
    istemp = initialize_output_directory(args)
    xml_parser = ET.XMLParser(remove_blank_text=True)
    doc = ET.parse(args.output_dir+'/bbf2.xml', xml_parser)
    if args.page_width == -1:
        args.page_width = float(doc.getroot().get('width'))
    if args.page_height == -1:
        args.page_height = float(doc.getroot().get('height'))
    if args.left == -1:
        args.left = 42 if args.mirror else 28
    if args.single and not args.scale_pages:
        args.scale_pages = 1
    used_images = {i.get('src').split('.')[0]:i for i in doc.findall("./section//image")}
    globbed = []
    for image in args.image_list:
        globbed = globbed + glob.glob(image)
    args.image_list = globbed
    media = load_media(args.output_dir, args.image_list)
    previews = []
    if not args.import_only:
        unused = [ImageInfo(name=entry.get('guid'),
                            src=entry.get('src'),
                            modified=entry.get('modified'),
                            width=int(entry.get('width')),
                            height=int(entry.get('height')))
                  for entry in media.findall("./images/media")
                  if not entry.get('guid') in used_images]
        pagenos = [int(page.get('number')) for page in empty_pages(doc)]
        if args.add_pages or not args.input:
            pagenos.extend(range(last_page(doc)+1, args.max_page))
        if args.section_field:
            populate_sections(doc, args, pagenos, unused, previews)
        else:
            populate_section(doc, args, pagenos, unused, previews)
    doc.write(args.output_dir+'/bbf2.xml', pretty_print=True)
    if args.preview:
        img_scale = float(args.preview_ppi)/72
        size = (int(args.page_width*img_scale), int(args.page_height*img_scale))
        slideshow.run_slideshow(previews, size)
    elif args.output:
        mergeBlurbFiles.merge(args.output_dir, args.output, create_thumbs=False)
    if istemp:
        shutil.rmtree(args.output_dir)


if __name__ == '__main__':
    main()

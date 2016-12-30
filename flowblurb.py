#!/usr/bin/env python
""" Populate a blurb book automatically using a 500px-like algorithm"""
import argparse
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
from datetime import datetime
from PIL import Image
import lxml.etree as ET
import extractBlurbFiles
import mergeBlurbFiles

ImageInfo = collections.namedtuple('ImageInfo', ('name', 'src', 'width', 'height', 'modified'))
ScaledImageInfo = collections.namedtuple('ScaledImageInfo', ('image', 'scale'))

class PageBuilder(object):
    """ Populate images onto pages. """
    def __init__(self, args, images):
        self.args = args
        self.images = images[::-1] # reverses the list, not in-place
        self.page_height = args.page_height-(args.top+args.bottom)

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
                page.append(
                    {
                        'x': str(x),
                        'y': str(y),
                        'width': str(img.image.width*img.scale),
                        'height': str(img.image.height*img.scale),
                        'type': 'image',
                        'transform': '1 0 0 1',
                        'id': str(uuid.uuid4()),
                        'name': img.image.name,
                        'scale': str(img.scale)
                    })
                x += img.image.width*img.scale + xspace
            y += row[0].image.height*row[0].scale + yspace
        return page

    def _preferred_size(self, width, height, _):
        scaledw = float(width) * self.args.image_height/height
        scaledh = self.args.image_height
        return (scaledw, scaledh)

    def _row_scale(self, row_width, page_width, row):
        whitespace = (len(row)-1) * self.args.xspace
        target_width = page_width - whitespace
        row_width -= whitespace
        return target_width/row_width

    def _get_row(self, page_width):
        row_width = 0
        row = []
        first_image = None
        while True:
            image = self.images.pop() if self.images else None
            if image is None:
                break
            if not first_image:
                first_image = image
            width = image.width
            height = image.height
            scaledw, scaledh = self._preferred_size(width, height, first_image)
            if row_width + scaledw > page_width:
                if row:
                    self.images.append(image)
                    row_width -= self.args.xspace      # we always added a trailing whitespace
                    # Required scale is what it takes to turn the current row width
                    # into the desired row width, but ignoring the whitespace which is not scaled
                    if self.args.scale:
                        scale = self._row_scale(row_width, page_width, row)
                        return [ScaledImageInfo(image=i.image, scale=i.scale*scale)
                                for i in row]
                    else:
                        return row
                else:
                    # Single image is too wide for row - scale h instead
                    scaledw = page_width
                    scaledh = height * scaledw/width
            row.append(ScaledImageInfo(image, scaledh/height))
            row_width += scaledw + self.args.xspace
        # leftover images are left unscaled unless the scale required to fill the row is small
        if row:
            scale = self._row_scale(row_width, page_width, row)
            if scale > 1.2:
                scale = 1.0
            return [ScaledImageInfo(image=i.image, scale=i.scale*scale)
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
            if row_height + height > self.page_height:
                self._unget_row(next_row)
                if self.args.overfill:
                    # An alternative approach to overfill is to treat it as a
                    # padding mathod in generate
                    self._unget_row(page_rows[-1])
                    page_rows.append(next_row)
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
        """ Return a page for each page nuber provided."""
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
            rows.sort(key=self._row_width, reverse=True)
            if last:
                last_row = rows.pop()
            new_rows = []
            for row in rows:
                if len(new_rows) % 2:
                    new_rows.append(row)
                else:
                    new_rows.insert(0, row)
            if last:
                new_rows.append(last_row)
            rows = new_rows
        return super(SmartPageBuilder, self)._generate_page(page_width, rows, pageno, first, last)

    def _preferred_size(self, width, height, image):
        """ What width/height would make this image have area self.image_height^2"""
        aspect = float(image.width)/image.height
        scaledh = self.args.image_height/math.sqrt(aspect)
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
    # last page and this one
    for page in range(last_page(doc), pageno-1):
        ET.SubElement(section, 'page', {'color':'#00000000', 'number': str(page)})
    return ET.SubElement(section, 'page', {'color':'#00000000', 'number': str(pageno)})

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
    required.add_argument('--input', type=argparse.FileType('r'),
                          help='Input blurb file')
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
    _add_invertable(layout_flags, 'double-only', 'Only populate double-page spreads')
    _add_invertable(layout_flags, 'odd-only', 'Only populate odd-numbered pages')
    _add_invertable(layout_flags, 'even-only', 'Only populate even-numbered pages')
    _add_invertable(layout_flags, 'overfill', 'Overfill pages')
    _add_invertable(layout_flags, 'add-pages', 'Add pages to existing book if necessary')

    sort_options = parser.add_argument_group('Sorting options')
    sort_options.add_argument('--random', action='store_true',
                              help='Shuffle images')
    sort_options.add_argument('--sort',
                              help='Sort order')
    sort_options.add_argument('--reverse', action='store_true',
                              help='Reverse image sort order')
    sort_options.add_argument('--exiftool', default='exiftool',
                              help='Location of exiftool (needed if sorting by exif fields')

    section_options = parser.add_argument_group('Miscellaneous options')
    section_options.add_argument("--sections", metavar='<section-name>', nargs='+',
                                 help='Names/order of sections to include')
    section_options.add_argument("--section-field", metavar='<exif-tag>',
                                 help='Exif tag used for sections')
    section_options.add_argument("--section-start", choices=['any', 'even', 'odd'],
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
    misc_options.add_argument('--config', metavar='<jsonfile>', type=jsonfile, action=LoadFromJson,
                              help='Load options from specified file')
    misc_options.add_argument('--no-default-config', action='store_true',
                              help='Do not try to load default configuration')
    misc_options.add_argument('--max-page', type=int, default=240,
                              help='Maximum page number')

    positional_options = parser.add_argument_group('Positional arguments')
    positional_options.add_argument('image_list', metavar='<image-file>', nargs='+',
                                    help='Image to add to blur book')

    if os.path.exists('./flowblurb.json') and '--no-default-config' not in sys.argv:
        args = parser.parse_args(['--config', './flowblurb.json']+sys.argv[1:])
    else:
        args = parser.parse_args()
    if args.double_only:
        args.double = True
    if args.output and os.path.exists(args.output) and not args.force:
        print 'Target file %s already exists' % args.output
        sys.exit()
    if not args.output:
        args.output = args.input
    if not (args.output or args.output_dir):
        print 'No target specified'
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
            container = ET.SubElement(page, 'container', img)
            ET.SubElement(container, 'image',
                          {'scale':img.get('scale'),
                           'x':'0', 'y':'0',
                           'autolayout':'fill',
                           'flip':'none',
                           'src':img.get('name')+'.jpg'
                          })
            del container.attrib['name']
            del container.attrib['scale']

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
            img.thumbnail((640, 640), Image.ANTIALIAS)
            if not os.path.exists("%s/thumbnails" % (project_dir)):
                os.makedirs("%s/thumbnails" % (project_dir))
            img.save("%s/thumbnails/%s.jpg" % (project_dir, guid))
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
            print 'Sorting by exiftool field %s' % args.sort
            with ExifTool(executable=args.exiftool) as exiftool:
                images = sorted(images,
                                key=lambda image: exiftool.get_tag(image.src, args.sort))
                if exiftool.missing_tags:
                    print 'Warning: exif tag %s not found in %d of %d images' % \
                          (args.sort, exiftool.missing_tags, len(images))
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
                print 'Target directory %s already exists' % args.output_dir
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

def populate_section(doc, args, pagenos, images):
    """ Populate all images for a single section. """
    images = sort_images(images, args)
    if args.smart:
        builder = SmartPageBuilder(args, images)
    else:
        if args.reverse:
            images = images[::-1]
        builder = PageBuilder(args, images)  # pylint: disable=I0011,R0204
    if args.section_start == 'even':
        while pagenos and (pagenos[0] % 2 == 1):
            pagenos.pop(0)
    elif args.section_start == 'odd':
        while pagenos and (pagenos[0] % 2 == 0):
            pagenos.pop(0)
    if args.section_add_title:
        add_title_page(doc, args, pagenos.pop(0))
    if args.section_blank_start:
        del pagenos[0:args.section_blank_start]
    populated = builder.get_pages(pagenos)
    if args.section_blank_end:
        del pagenos[0:args.section_blank_end]
    update_blurb(doc, populated)

def populate_sections(doc, args, pagenos, images):
    """ Populate all images for multiple sections. """
    sections_map = find_sections(args, images)
    if not args.sections:
        args.sections = sorted(sections_map.keys())
    for section in args.sections:
        if section in sections_map:
            section_args = argparse.Namespace(**vars(args))
            setattr(section_args, 'section_title', section)
            if os.path.isfile(section+'.json'):
                with open(section+'.json') as json_data:
                    new_args = json.load(json_data)
                    for key, value in new_args.iteritems():
                        key = key.replace('-', '_')
                        setattr(section_args, key, value)
            populate_section(doc, section_args, pagenos, sections_map[section])

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
    used_images = {i.get('src').split('.')[0]:i for i in doc.findall(".//image")}

    media = load_media(args.output_dir, args.image_list)
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
            populate_sections(doc, args, pagenos, unused)
        else:
            populate_section(doc, args, pagenos, unused)
    doc.write(args.output_dir+'/bbf2.xml', pretty_print=True)
    if args.output:
        mergeBlurbFiles.merge(args.output_dir, args.output)
    if istemp:
        shutil.rmtree(args.output_dir)


if __name__ == '__main__':
    main()

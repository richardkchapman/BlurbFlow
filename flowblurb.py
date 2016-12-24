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
        #self.page_width = args.page_width-(args.left+args.right)

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

    def _generate_page(self, page_width, rows, pageno, first):
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
                        whitespace = (len(row)-1) * self.args.xspace
                        target_width = page_width - whitespace
                        row_width -= whitespace
                        scale = target_width/row_width
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
        return row if row else None

    def _unget_all_row(self, row):
        if row:
            for i in row:
                self.images.append(i.image)

    def _unget_row(self, row):
        self._unget_all_row(row)

    def next_page(self, page_width, pageno, first):
        """ Get a page from the image list. """
        height = 0
        page_rows = []
        while True:
            next_row = self._get_row(page_width)
            if next_row is None:
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
        height -= self.args.yspace
        return self._generate_page(page_width, page_rows, pageno, first)

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
                populated = self.next_page(page_width, pageno, first)
                first = False
                if populated:
                    yield (populated, pageno, double)

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

    def _generate_page(self, page_width, rows, pageno, first):
        if not self.args.scale:
            # sort the rows to a more pleasing order - wide in the middle
            rows.sort(key=self._row_width, reverse=True)
            new_rows = []
            for row in rows:
                if len(new_rows) % 2:
                    new_rows.append(row)
                else:
                    new_rows.insert(0, row)
            rows = new_rows
        return super(SmartPageBuilder, self)._generate_page(page_width, rows, pageno, first)

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
            self.last_row = self.all_rows.pop() if self.all_rows else None
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

    def __enter__(self):
        self.process = subprocess.Popen(
            [self.executable, "-stay_open", "True", "-@", "-"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
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

def find_page(doc, pageno):
    """ Find specific page in blurb document. """
    (element,) = doc.xpath('.//section/page[@number=\'%d\']' % pageno)
    # Will throw error if not exactly one match.
    return element

def empty_pages(doc):
    """ Returns all empty pages in the original Blurb doc. """
    for page in doc.findall('.//section/page'):
        if not page.findall('container'):
            yield page

def parse_args():
    """ Command line parsing code"""

    class LoadFromJson(argparse.Action):  # pylint: disable=I0011,R0903
        """ argparse action to support reading options from a json dictionary. """
        def __call__(self, parser, namespace, values, option_string=None):
            for key, value in values.iteritems():
                if key != option_string.lstrip('-'):
                    setattr(namespace, key, value)

    def jsonfile(filename):
        """ Argparse helper for parsing json files. """
        try:
            with open(filename) as json_data:
                return json.load(json_data)
        except:
            raise argparse.ArgumentTypeError('Invalid option file %s' % filename)

    parser = argparse.ArgumentParser(description='Update blurb files', add_help=False)
    parser.add_argument('--help', action='help', help='show this help message and exit')
    parser.add_argument('-h', '--height', dest='page_height', metavar='N', type=float, default=-1,
                        help='Maximum height of a page (default: read from blurb doc)')
    parser.add_argument('-w', '--width', dest='page_width', metavar='N', type=float, default=-1,
                        help='Maximum width of a page (default: read from blurb doc)')
    parser.add_argument('-i', '--imageHeight', dest='image_height', metavar='N', type=float,
                        default=200.0, help='Minimum height of an image (default: %(default).0f)')
    parser.add_argument('--xspace', dest='xspace', metavar='N', type=float, default=0.0,
                        help='X gap between images (default: %(default).0f)')
    parser.add_argument('--yspace', dest='yspace', metavar='N', type=float, default=0.0,
                        help='Y gap between images (default: %(default).0f)')
    parser.add_argument('--left', dest='left', metavar='N', type=float, default=-1.0,
                        help='Left margin')
    parser.add_argument('--right', dest='right', metavar='N', type=float, default=28.0,
                        help='Right margin')
    parser.add_argument('--top', dest='top', metavar='N', type=float, default=28.0,
                        help='Top margin')
    parser.add_argument('--bottom', dest='bottom', metavar='N', type=float, default=28,
                        help='Bottom margin')
    parser.add_argument('--mirror', dest='mirror', action='store_true',
                        help='Mirror left/right margins on even/odd pages')
    parser.add_argument('--smart', dest='smart', action='store_true', help='Smart layout mode')
    parser.add_argument('--ycenter', dest='ycenter', action='store_true',
                        help='Center rows vertically within page')
    parser.add_argument('--xcenter', dest='xcenter', action='store_true',
                        help='Center rows horizontally within page')
    parser.add_argument('--xspread', dest='xspread', action='store_true',
                        help='Spread images horizontally within rows')
    parser.add_argument('--yspread', dest='yspread', action='store_true',
                        help='Spread rows vertically within pages')
    parser.add_argument('--scale', dest='scale', action='store_true',
                        help='Scale images within rows to fill width')
    parser.add_argument('--double', dest='double', action='store_true',
                        help='Use double-page spreads where possible')
    parser.add_argument('--double-only', action='store_true',
                        help='Only populate double-page spreads')
    parser.add_argument('--overfill', action='store_true',
                        help='Overfill pages')
    parser.add_argument('--sort', choices=['key', 'date', 'size', 'name', 'rating', 'exif'],
                        help='Sort order')
    parser.add_argument('--reverse', action='store_true',
                        help='Reverse image sort order')
    parser.add_argument('--sort_key',
                        help='Exif field to sort by when --sort=exif specified')
    parser.add_argument('--exiftool', default='exiftool',
                        help='Location of exiftool (needed if sorting by exif fields')
    parser.add_argument('-o', '--output', dest='output', help='Output filename')
    parser.add_argument('-f', '--force', dest='force', help='Force overwrite', action='store_true')
    parser.add_argument('--image-list', nargs='+')
    parser.add_argument('--config', type=jsonfile, action=LoadFromJson)
    parser.add_argument('target')
    if os.path.exists('./flowblurb.json'):
        args = parser.parse_args(['--config', './flowblurb.json']+sys.argv[1:])
    else:
        args = parser.parse_args()
    if args.double_only:
        args.double = True
    if args.sort_key and not args.sort:
        args.sort = 'exif'
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

def populate_media(project_dir, media, images):
    """ Add listed images to the media_registry xml. """
    (parent,) = media.xpath('./images')
    changed = False
    for image_name in images:
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
            shutil.copyfile(image_name, "%s/images/%s.%s" % (project_dir, guid, ext))
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

def get_exif(imgfile):
    """ Read exif info using exiftool. """
    print "get_exif('%s')" % imgfile
    tags = {}
    pipe = os.popen('exiftool \"' + imgfile + '\"')
    for line in pipe:
        the_split = line.split(' :')
        if len(the_split) != 2:
            the_split = line.split(':')
        tags[the_split[0].strip()] = the_split[1].strip()
    pipe.close()
    return tags

def sort_images(images, args):
    """ Sort images according to requested order. """
    if args.sort:
        if args.sort == 'name':
            return sorted(images, key=lambda image: image.src)
        elif args.sort == 'date':
            return sorted(images, key=lambda image: image.modified)
        elif args.sort == 'size':
            return sorted(images, key=lambda image: int(image.width)*int(image.height))
        elif args.sort == 'rating':
            args.sort_key = 'XMP:Rating'
            args.sort = 'exif'
        if args.sort == 'exif' and args.sort_key:
            with ExifTool(executable=args.exiftool) as exiftool:
                return sorted(images,
                              key=lambda image: exiftool.tags(image.src).get(args.sort_key, None))
    else:
        return images

def main():
    """ Main code. """
    args = parse_args()
    if args.output and os.path.exists(args.output) and not args.force:
        print 'Target file %s already exists' % args.target
        sys.exit()
    if os.path.isfile(args.target):
        extracted = tempfile.mkdtemp(prefix=args.target)
        istemp = True
        extractBlurbFiles.extract(args.target, extracted)
    else:
        extracted = args.target
        istemp = False
    create_backup(extracted)
    xml_parser = ET.XMLParser(remove_blank_text=True)
    doc = ET.parse(extracted+'/bbf2.xml', xml_parser)
    if args.page_width == -1:
        args.page_width = float(doc.getroot().get('width'))
    if args.page_height == -1:
        args.page_height = float(doc.getroot().get('height'))
    if args.left == -1:
        args.left = 42 if args.mirror else 28
    used_images = {i.get('src').split('.')[0]:i for i in doc.findall(".//image")}

    media = load_media(extracted, args.image_list)
    unused = [ImageInfo(name=entry.get('guid'),
                        src=entry.get('src'),
                        modified=entry.get('modified'),
                        width=int(entry.get('width')),
                        height=int(entry.get('height')))
              for entry in media.findall("./images/media")
              if not entry.get('guid') in used_images]

    if args.smart:
        builder = SmartPageBuilder(args, unused)
    else:
        unused = sort_images(unused, args)
        if args.reverse:
            unused = unused[::-1]
        builder = PageBuilder(args, unused)  # pylint: disable=I0011,R0204

    pagenos = [int(page.get('number')) for page in empty_pages(doc)]
    populated = builder.get_pages(pagenos)
    update_blurb(doc, populated)
    doc.write(extracted+'/bbf2.xml', pretty_print=True)
    if args.output:
        mergeBlurbFiles.merge(extracted, args.output)
    if istemp:
        shutil.rmtree(extracted)


if __name__ == '__main__':
    main()

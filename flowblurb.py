#!/usr/bin/env python
""" Populate a blurb book automatically using a 500px-like algorithm"""
import argparse
import os
import sys
import math
import uuid
import collections
import lxml.etree as ET
import extractBlurbFiles
import mergeBlurbFiles

ImageInfo = collections.namedtuple('ImageInfo', ('name', 'src', 'width', 'height'))
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
        return row_width + self.args.xdelta*num_deltas

    def _generate_page(self, page_width, rows, current_page_height, pageno):
        page = []
        y = self.args.top
        ydelta = self.args.ydelta
        if self.args.spread and len(rows) > 1:
            ydelta += (self.page_height - current_page_height)/(len(rows)-1)
        elif self.args.center:
            y += (self.page_height - current_page_height)/2
        for row in rows:
            if self.args.mirror and pageno%2 == 0:
                x = self.args.right
            else:
                x = self.args.left
            xdelta = self.args.xdelta
            row_width = self._row_width(row)
            if self.args.spread and len(row) > 1:
                xdelta += (page_width - row_width)/(len(row)-1)
            elif self.args.center:
                x += (page_width - row_width)/2
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
                x += img.image.width*img.scale + xdelta
            y += row[0].image.height*row[0].scale + ydelta
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
                    row_width -= self.args.xdelta      # we always added a trailing whitespace
                    # Required scale is what it takes to turn the current row width
                    # into the desired row width, but ignoring the whitespace which is not scaled
                    if self.args.scale:
                        whitespace = (len(row)-1) * self.args.xdelta
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
            row_width += scaledw + self.args.xdelta
        return row if row else None

    def _unget_all_row(self, row):
        if row:
            for i in row:
                self.images.append(i.image)

    def _unget_row(self, row):
        self._unget_all_row(row)

    def next_page(self, page_width, pageno):
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
                break
            page_rows.append(next_row)
            height += row_height + self.args.ydelta
        height -= self.args.ydelta
        return self._generate_page(page_width, page_rows, height, pageno)

    def get_pages(self, pagenos):
        """ Return a page for each page nuber provided."""
        i = 0
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
            populated = self.next_page(page_width, pageno)
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
        super(SmartPageBuilder, self).__init__(args, images)
        self.all_rows = None
        self.mode = 0
        self.last_row = None
        self.next_row = None
        self.last_width = 0

    def _generate_page(self, page_width, rows, current_page_height, pageno):
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
        return super(SmartPageBuilder, self)._generate_page(page_width, rows,
                                                            current_page_height,
                                                            pageno)

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
            ret = self.next_row
            self.next_row = None
            return ret
        if not self.all_rows:
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
        if self.next_row:
            print "Uh oh"
        self.next_row = row

    def next_page(self, page_width, pageno):
        if page_width != self.last_width:
            if self.all_rows:
                for row in self.all_rows:
                    self._unget_all_row(row)
            self._unget_all_row(self.last_row)
            self._unget_all_row(self.next_row)
            self.all_rows = None
            self.last_row = None
            self.next_row = None
        self.last_width = page_width
        return super(SmartPageBuilder, self).next_page(page_width, pageno)

def find_page(doc, pageno):
    """ Find specific page in blurb document. """
    (element,) = doc.xpath('.//section/page[@number=\'%d\']' % pageno)
    # Will throw error if not exatly one match.
    return element

def empty_pages(doc):
    """ Returns all empty pages in the original Blurb doc. """
    for page in doc.findall('.//section/page'):
        if not page.findall('container'):
            yield page

def parse_args():
    """ Command line parsing code"""
    parser = argparse.ArgumentParser(description='Update blurb files', add_help=False)
    parser.add_argument('--help', action='help', help='show this help message and exit')
    parser.add_argument('-h', '--height', dest='page_height', metavar='N', type=float, default=-1,
                        help='Maximum height of a page (default: read from blurb doc)')
    parser.add_argument('-w', '--width', dest='page_width', metavar='N', type=float, default=-1,
                        help='Maximum width of a page (default: read from blurb doc)')
    parser.add_argument('-i', '--imageHeight', dest='image_height', metavar='N', type=float,
                        default=200.0, help='Minimum height of an image (default: %(default).0f)')
    parser.add_argument('-l', '--left', dest='left', metavar='N', type=float, default=-1.0,
                        help='Left margin')
    parser.add_argument('-r', '--right', dest='right', metavar='N', type=float, default=28.0,
                        help='Right margin')
    parser.add_argument('-t', '--top', dest='top', metavar='N', type=float, default=28.0,
                        help='Top margin')
    parser.add_argument('-b', '--bottom', dest='bottom', metavar='N', type=float, default=28,
                        help='Bottom margin')
    parser.add_argument('-dx', '--xdelta', dest='xdelta', metavar='N', type=float, default=0.0,
                        help='X gap between images (default: %(default).0f)')
    parser.add_argument('-dy', '--ydelta', dest='ydelta', metavar='N', type=float, default=0.0,
                        help='Y gap between images (default: %(default).0f)')
    parser.add_argument('-v', '--vertical', dest='vertical', action='store_true',
                        help='Vertical layout mode')
    parser.add_argument('-m', '--mirror', dest='mirror', action='store_true',
                        help='Mirror left/right margins on even/odd pages')
    parser.add_argument('--verbose', dest='verbose', action='store_true', help='Verbose mode')
    parser.add_argument('--smart', dest='smart', action='store_true', help='Smart sort mode')
    parser.add_argument('-c', '--center', dest='center', action='store_true',
                        help='Center resulting page within space')
    parser.add_argument('--spread', dest='spread', action='store_true',
                        help='Spread resulting rows within space')
    parser.add_argument('--scale', dest='scale', action='store_true',
                        help='Scale resulting rows to fill space')
    parser.add_argument('--double', dest='double', action='store_true',
                        help='Use double-page spreads where possible')
    parser.add_argument('-o', '--output', dest='output', help='Output filename')
    parser.add_argument('-f', '--force', dest='force', help='Force overwrite', action='store_true')

    parser.add_argument('target')
    return parser.parse_args()

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

def main():
    """ Main code. """
    args = parse_args()
    if os.path.exists(args.output) and not args.force:
        print 'Target file %s already exists' % args.target
        sys.exit()
    if os.path.isfile(args.target):
        extracted = args.target + '.tmp'
        extractBlurbFiles.extract(args.target, extracted)
    else:
        extracted = args.target
    xml_parser = ET.XMLParser(remove_blank_text=True)
    doc = ET.parse(extracted+'/bbf2.xml', xml_parser)
    if args.page_width == -1:
        args.page_width = float(doc.getroot().get('width'))
    if args.page_height == -1:
        args.page_height = float(doc.getroot().get('height'))
    if args.left == -1:
        args.left = 42 if args.mirror else 28
    used_images = {i.get('src').split('.')[0]:i for i in doc.findall(".//image")}

    media = ET.parse(extracted+'/media_registry.xml')
    unused = [ImageInfo(name=entry.get('guid'),
                        src=entry.get('src'),
                        width=int(entry.get('width')),
                        height=int(entry.get('height')))
              for entry in media.findall("./images/media")
              if not entry.get('guid') in used_images]

    if args.smart:
        unused.sort(key=lambda image: float(image.width)/image.height, reverse=True)
        builder = SmartPageBuilder(args, unused)
    else:
        unused.sort(key=lambda image: image.src)
        builder = PageBuilder(args, unused)  # pylint: disable=I0011,R0204

    pagenos = [int(page.get('number')) for page in empty_pages(doc)]
    populated = builder.get_pages(pagenos)
    update_blurb(doc, populated)
    doc.write(extracted+'/bbf2.xml', pretty_print=True)
    if args.output:
        mergeBlurbFiles.merge(extracted, args.output)


if __name__ == '__main__':
    main()

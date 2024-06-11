#!/usr/bin/env python3
from __future__ import print_function
""" Append one blurb bool to another"""
import os
import argparse
import sys
import math
import uuid
import collections
import tempfile
import shutil
import lxml.etree as ET

from datetime import datetime
import extractBlurbFiles
import mergeBlurbFiles

output_dir = ""
doc = None
reg = None

def copyAndReguid(temp_dir, dir, guidmap):
    global output_dir
    global doc
    global reg
    fulldir = temp_dir+'/'+dir
    for name in os.listdir(fulldir):
        source = os.path.join(fulldir, name)
        oldguid, ext = os.path.splitext(name)
        dest = ""
        if oldguid in guidmap:
            newname = guidmap[oldguid]+ext
            dest = os.path.join(output_dir+'/'+dir, newname)
            print ('Reusing new guid when copying %s to %s ' % (name, newname))
        else:
            newname = name
            newguid = None
            dest = os.path.join(output_dir+'/'+dir, newname)
            while os.path.isfile(dest):
                newguid = str(uuid.uuid4())
                newname = newguid+ext
                dest = os.path.join(output_dir+'/'+dir, newname)
            if newguid:
                guidmap[oldguid] = newguid
                print ('Mapping %s to %s ' % (name, newname))
        shutil.copyfile(source, dest)
        utime = os.stat(source).st_mtime
        os.utime(dest, (utime, utime))

def concatBlurb(infile):
    global output_dir
    global doc
    global reg
    guidmap = {}
    temp_dir = tempfile.mkdtemp()
    extractBlurbFiles.extract(infile, temp_dir)
    # First copy any files, reguiding as needed
    for dir in ["images", "thumbnails_small", "textflows"]:
        copyAndReguid(temp_dir, dir, guidmap)
    xml_parser = ET.XMLParser(remove_blank_text=True, strip_cdata=False)
    #Copy any texflow elements, using new guids. Reguid any container ids in them
    mydoc = ET.parse(temp_dir+'/bbf2.xml', xml_parser)
    lastflow = doc.xpath('.//textflow')[-1]
    for flow in mydoc.findall('.//textflow'):
        guid = flow.get('src')
        if guid in guidmap:
            print('Guid needs remapping')
            guid = guidmap[guid]
            flow.set('src', guid)
        for container in flow.findall('.//container-id'):
            guid = container.text
            newguid = str(uuid.uuid4())
            container.text = newguid
            guidmap[guid] = newguid
            print ('Mapping container ref %s to %s ' % (guid, newguid))
        lastflow.addnext(flow)
        lastflow = flow
    #Copy any page elements, using new container and image guids, and renumbering.
    lastpage = doc.xpath('.//page')[-1]
    # MORE - add a blank page if needed to ensure nextPageNo is odd
    nextPageNo = int(lastpage.get('number'))+1
    if nextPageNo % 2 == 0:
        print("Need to start with even page count (count is %d)" % nextPageNo)
        exit(1)
    for page in mydoc.findall('.//page'):
        if int(page.get('number'))==-1:
            continue
        for container in page.findall('.//container'):
            guid = container.get('id')
            if guid in guidmap:
                newguid = guidmap[guid]
                container.set('id', newguid)
                print ('Mapping container id %s to %s ' % (guid, newguid))
        for image in page.findall('.//image'):
            guid, ext = os.path.splitext(image.get('src'))
            if guid in guidmap:
                newguid = guidmap[guid]
                image.set('src', newguid+ext)
                print ('Mapping image id %s to %s ' % (guid+ext, newguid+ext))
        page.set('number', str(nextPageNo))
        nextPageNo = nextPageNo+1
        lastpage.addnext(page)
        lastpage = page
    myreg = ET.parse(temp_dir+'/media_registry.xml', xml_parser)
    lastmedia = reg.xpath('./images/media')[-1]
    for media in myreg.findall('./images/media'):
        guid = media.get('guid')
        if guid in guidmap:
            newguid = guidmap[guid]
            media.set('guid', newguid)
            print ('Mapping image media id %s to %s ' % (guid, newguid))
        lastmedia.addnext(media)
        lastmedia = media
    lastmedia = reg.xpath('./text/media')[-1]
    for media in myreg.findall('./text/media'):
        guid = media.get('guid')
        if guid in guidmap:
            newguid = guidmap[guid]
            media.set('guid', newguid)
            print ('Mapping flow media id %s to %s ' % (guid, newguid))
        lastmedia.addnext(media)
        lastmedia = media
    shutil.rmtree(temp_dir)

def last_page(doc):
    """ Returns last page no in the original Blurb doc. """
    pages = doc.xpath('.//section/page')
    return int(pages[-1].get('number'))

def main():
    """ Main code. """
    global output_dir
    global doc
    global reg
    global guidmap
    output_dir = tempfile.mkdtemp()
    parser = argparse.ArgumentParser(description='Concatenate blurb files')
    parser.add_argument('-f', '--force', dest='force', help='Force overwrite', action='store_true')
    parser.add_argument('target')
    parser.add_argument('sources', nargs='+')
    args = parser.parse_args()
    if os.path.exists(args.target) and not args.force:
        print('Target file %s already exists' % args.target)
        sys.exit()

    extractBlurbFiles.extract(args.sources[0], output_dir)
    xml_parser = ET.XMLParser(remove_blank_text=True, strip_cdata=False)
    doc = ET.parse(output_dir+'/bbf2.xml', xml_parser)
    reg = ET.parse(output_dir+'/media_registry.xml', xml_parser)
    for infile in args.sources[1:]:
        concatBlurb(infile)
    doc.write(output_dir+'/bbf2.xml', pretty_print=True)
    reg.write(output_dir+'/media_registry.xml', pretty_print=True)
    mergeBlurbFiles.merge(output_dir, args.target, add_missing=False, create_thumbs=False, template=None)
    shutil.rmtree(output_dir)


if __name__ == '__main__':
    main()

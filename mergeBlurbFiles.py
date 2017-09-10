#!/usr/bin/env python
"""This script merges files from a directory into a .blurb database"""

import sqlite3
import os
import sys
import argparse
import tempfile
from datetime import datetime
from PIL import Image
import lxml.etree as ET

def _insert_file(conn, fullpath, filepath):
    with open(fullpath, 'rb') as my_file:
        filecontent = my_file.read()
    filedate = datetime.fromtimestamp(os.path.getmtime(fullpath))
    sql = "INSERT INTO Files (filepath, filecontent, filesize, filedate) " \
          "values (:filepath, :filecontent, :filesize, :filedate);"
    conn.execute(sql, {'filepath': filepath,
                       'filecontent':buffer(filecontent),
                       'filesize':len(filecontent),
                       'filedate':filedate.isoformat(' ')})
    conn.commit()

def _update_version(conn, version):
    conn.cursor().execute("INSERT INTO ArchiveVersion (version) VALUES (%d);" % version)
    conn.commit()

def _populate_image(conn, source_dir, guid, original, create_thumbs):
    ext = os.path.splitext(original)[1][1:].lower()
    localname = "/images/%s.%s" % (guid, ext)
    if not os.path.exists(source_dir+localname):
        _insert_file(conn, original, localname)
    if create_thumbs:
        localname = "/thumbnails/%s.jpg" % guid
        if not os.path.exists(source_dir+localname):
            img = Image.open(original)
            img.thumbnail((640, 640), Image.ANTIALIAS)
            with tempfile.NamedTemporaryFile() as temp:
                img.save(temp, format='JPEG')
                _insert_file(conn, temp.name, localname)

def _add_missing_images(conn, source, create_thumbs):
    media = ET.parse(source+'/media_registry.xml')
    doc = ET.parse(source+'/bbf2.xml')
    used_images = {i.get('src').split('.')[0]:i for i in doc.findall(".//image")}
    for entry in media.findall("./images/media"):
        if entry.get('guid') in used_images:
            _populate_image(conn, source, entry.get('guid'), entry.get('src'), create_thumbs)

def merge(source, target, add_missing=True, create_thumbs=False):
    """ Merge files from directory 'source' to blurb file 'target'. """
    if os.path.exists(target):
        os.remove(target)
    conn = sqlite3.connect(target)
    conn.cursor().execute("CREATE TABLE 'ArchiveVersion' ('version' NUM);")
    conn.cursor().execute("CREATE TABLE 'Files' ('filepath' TEXT NOT NULL UNIQUE, " \
                          "'filecontent' BLOB , 'filesize' NUM NOT NULL DEFAULT -1, " \
                          "'filedate' TEXT NOT NULL DEFAULT 'Unknown');")
    conn.commit()

    for root, _, files in os.walk(source):
        for fname in files:
            subroot = root[len(source):]
            subfile = subroot+'/'+fname if len(subroot) else fname
            if subfile == '.version':
                with open(root+'/'+fname, 'r') as input_file:
                    version = int(input_file.read())
                    _update_version(conn, version)
            else:
                fullfile = root+'/'+fname
                _insert_file(conn, fullfile, subfile)
    if add_missing:
        _add_missing_images(conn, source, create_thumbs)

def _main():
    parser = argparse.ArgumentParser(description='Merge blurb files')
    parser.add_argument('-f', '--force', dest='force', help='Force overwrite', action='store_true')
    parser.add_argument('--add-missing', help='Add missing images', action='store_true')
    parser.add_argument('--add-thumbnails', help='Add missing thumbnails', action='store_true')
    parser.add_argument('source')
    parser.add_argument('target')
    args = parser.parse_args()
    if os.path.exists(args.target) and not args.force:
        print 'Target file %s already exists' % args.target
        sys.exit()
    merge(args.source, args.target, args.add_missing, args.add_thumbnails)

if __name__ == '__main__':
    _main()

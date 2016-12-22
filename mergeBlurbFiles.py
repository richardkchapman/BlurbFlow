#!/usr/bin/env python
"""This script merges files from a directory into a .blurb database"""

import sqlite3
import os
import sys
import argparse
from datetime import datetime

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

def merge(source, target):
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

def _main():
    parser = argparse.ArgumentParser(description='Merge blurb files')
    parser.add_argument('-f', '--force', dest='force', help='Force overwrite', action='store_true')
    parser.add_argument('source')
    parser.add_argument('target')
    args = parser.parse_args()
    if os.path.exists(args.target) and not args.force:
        print 'Target file %s already exists' % args.target
        sys.exit()
    merge(args.source, args.target)

if __name__ == '__main__':
    _main()

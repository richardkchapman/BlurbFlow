#!/usr/bin/env python
"""This script extracts files from a .blurb database into a diretory
   for subsequent processing"""

import sqlite3
import os
import argparse
from datetime import datetime
import time

def extract_files(path, cursor):
    """Extract all files listed in the Files table."""
    sql = "SELECT filepath, filecontent,filesize,filedate FROM Files;"
    cursor.execute(sql)
    for row in cursor:
        filepath = path+'/'+row['filepath']
        filedir = os.path.dirname(filepath)
        filesize = row['filesize']
        filedate = row['filedate']
        if not os.path.exists(filedir):
            os.makedirs(filedir)
        with open(filepath, 'wb') as output_file:
            if filesize == -1:
                print('Missing size')
                output_file.write(row['filecontent'])
            else:
                output_file.write(row['filecontent'][0:row['filesize']])
        timestamp = datetime.strptime(filedate, "%Y-%m-%d %H:%M:%S")
        utime = time.mktime(timestamp.timetuple())
        os.utime(filepath, (utime, utime))

def extract_archive_version(path, cursor):
    """Extract the "ArchiveVersion" table."""
    sql = "SELECT version FROM ArchiveVersion;"
    cursor.execute(sql)
    version = cursor.fetchone()[0]
    filepath = path+'/.version'
    with open(filepath, 'w') as output_file:
        output_file.write(str(version)+'\n')

def extract(source, target):
    """Extract files from .blurb database "source" to directory "target"."""
    conn = sqlite3.connect(source)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    extract_files(target, cur)
    cur = conn.cursor()
    extract_archive_version(target, cur)
    conn.close()

def main():
    """Main body if run standalone."""
    parser = argparse.ArgumentParser(description='Extract blurb files')
    parser.add_argument('source')
    parser.add_argument('target')
    args = parser.parse_args()
    extract(args.source, args.target)

if __name__ == '__main__':
    main()

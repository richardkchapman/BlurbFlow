# BlurbFlow
Automatic layout of a Blurb Photo Book

Blurb (www.blurb.com) is a Photo book printing service, which supports offline
editing of book files using the BookWright program.

However, editing a complex layout is time-consuming and error-prone. I was trying
to create a book that used a layout similar to that used by 500px and Google Images
search, in order to cleanly display a collection of images of various aspect ratios
without clipping any of them.

The file format (.blurb) used by BookWright is easily modified - it's basically a
directory of files all packeged into a sqlite database, with the book layout 
stored in XML with a fairly transparent schema.

extractBlurbFiles.py can be used to split a .blurb file into the corresponding
directory, and mergeBlurbFiles.py performs the reverse operation.

flowblurb.py will automatically populate any blank pages in an existing .blurb file
using unused images previously imported into the blurb book. Play with the options until
you find one you like...

Note: I have no connection with Blurb.com (except as a potential customer), and this program
is not endorsed or supported by them in any way. Use at your own risk.
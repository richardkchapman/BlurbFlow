# BlurbFlow
Algorithmic layout of a images for a Photo Book

Blurb (www.blurb.com) is a Photo book printing service, which supports offline
editing of book files using the BookWright program.

However, editing a complex layout is time-consuming and error-prone. I was trying
to create a book that used a layout similar to that used by 500px and Google Images
search, in order to cleanly display a collection of images of various aspect ratios
without clipping any of them.

The file format (.blurb) used by BookWright is easily modified - it's basically a
directory of files all packaged into a sqlite database, with the book layout 
stored in XML with a fairly transparent schema.

extractBlurbFiles.py can be used to split a .blurb file into the corresponding
directory, and mergeBlurbFiles.py performs the reverse operation.

flowblurb.py will automatically populate any blank pages in an existing .blurb file
using unused images previously imported into the blurb book. Play with the options until
you find one you like...

The basic principle of the algorithm is to group images into rows where each image in a
row is scaled to be the same height, then populate rows onto pages. The simplest form of
the command does no more than that, but options are available to make all rows the same width,
by scaling the images or spreading them out, or to center the rows for a more aesthetic
appearance.

In 'smart' mode the order of images and the layout algorithm is adjusted in order to try to
mitigate one of the problems of the simple flow algorithm, namely that images in 'portrait'
orientation end up scaled much smaller than ones in landscape. This is done by adjusting the
target height of each row according to the aspect ratio of the first image in the row, in 
order to give equal area to each image, and by sorting the images so that all images on a
row have similar aspect ratios. Rows are then shuffled in order to try to give a mixture
of shapes on each page.

Note: I have no connection with Blurb.com (except as a potential customer), and this program
is not endorsed or supported by them in any way. Use at your own risk.
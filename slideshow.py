#!/usr/bin/env python
"""Show slideshow for images in a given directory (recursively) in cycle.

If no directory is specified, it uses the current directory.

Based on a gist at https://gist.github.com/zed/8b05c3ea0302f0e2c14c
"""
import os
import platform
import sys

try:
    import tkinter as tk
except ImportError:  # Python 2
    import Tkinter as tk  # $ sudo apt-get install python-tk

from PIL import Image  # $ pip install pillow
from PIL import ImageTk

class Slideshow(object):

    def __init__(self, parent, filenames, slideshow_delay=20):
        self.main = parent.winfo_toplevel()
        self.filenames = filenames
        self._delay = slideshow_delay*1000
        self._index = 0
        self._id = None
        self._photo_image = None  # must hold reference to PhotoImage
        self.imglbl = tk.Label(parent)  # it contains current image
        # label occupies all available space
        self.imglbl.pack(fill=tk.BOTH, expand=True)
        self._show_image_on_next_tick()

    def _slideshow(self):
        self._index += 1
        if self._index >= len(self.filenames):
            self._index = 0
        self.show_image()

    def show_image(self):
        filename = self.filenames[self._index]
        image = Image.open(filename)  # note: let OS manage file cache

        # shrink image inplace to fit in the application window
        width, height = self.main.winfo_width(), self.main.winfo_height()
        if image.size[0] > width or image.size[1] > height:
            # note: ImageOps.fit() copies image
            # preserve aspect ratio
            if width < 3 or height < 3:  # too small
                return  # do nothing
            image.thumbnail((width - 2, height - 2), Image.ANTIALIAS)

        # note: pasting into an RGBA image that is displayed might be slow
        # create new image instead
        self._photo_image = ImageTk.PhotoImage(image)
        self.imglbl.configure(image=self._photo_image)
        self._id = self.imglbl.after(self._delay, self._slideshow)

        # set application window title
        self.main.wm_title(filename)

    def _show_image_on_next_tick(self):
        # cancel previous callback schedule a new one
        if self._id is not None:
            self.imglbl.after_cancel(self._id)
        self._id = self.imglbl.after(1, self.show_image)

    def next_image(self, _=None):
        self._index += 1
        if self._index >= len(self.filenames):
            self._index = 0
        self._show_image_on_next_tick()

    def prev_image(self, _=None):
        if not self._index:
            self._index = len(self.filenames)
        self._index -= 1
        self._show_image_on_next_tick()

    def fit_image(self, event=None):
        """Fit image inside application window on resize."""
        if event is not None and event.widget is self.main:
            self._show_image_on_next_tick()


def run_slideshow(image_filenames):
    root = tk.Tk()
    width, height, xoffset, yoffset = 400, 300, 0, 0
    root.geometry("%dx%d%+d%+d" % (width, height, xoffset, yoffset))

    try:  # start slideshow
        app = Slideshow(root, image_filenames, slideshow_delay=2000)
    except StopIteration:
        sys.exit("no image files found")

    # configure keybindings
    root.bind("<Escape>", lambda _: root.destroy())  # exit on Esc
    root.bind('<Prior>', app.prev_image)
    root.bind('<Up>', app.prev_image)
    root.bind('<Left>', app.prev_image)
    root.bind('<Next>', app.next_image)
    root.bind('<Down>', app.next_image)
    root.bind('<Right>', app.next_image)

    root.bind("<Configure>", app.fit_image)  # fit image on resize
    root.focus_set()
    if platform.system() == 'Darwin':  # How Mac OS X is identified by Python
        os.system('''/usr/bin/osascript -e 'tell app "Finder" to set frontmost of process "Python" to true' ''')
    root.mainloop()

if __name__ == '__main__':
    run_slideshow(sys.argv[1:])

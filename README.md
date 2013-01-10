Get iPlayer for Totem
=====================

This is a plugin for [Totem](http://projects.gnome.org/totem/index.html) to allow easier
use of the command line program [get_iplayer](http://linuxcentre.net/getiplayer). The hard
work is provided by get_iplayer - this plugin only provides a simplified frontend.

Compatibility and Requirements
------------------------------

This is currently developed under my own Gentoo Linux environment. The product is in alpha
state and has not been tested yet outside of my own machine, though I would expect it to run
on any Unix-like operating system that Totem will run on.

You are required to install get_iplayer separately - take this from the
[git repository](http://git.infradead.org/get_iplayer.git) as I unwisely created the front end
for a bleeding edge version (commit 64e7c68de92611afb371ab068b18a3867ca6406a).

For flash streams you will also need [flvstreamer](http://savannah.nongnu.org/projects/flvstreamer/) and/or
[rtmpdump](http://rtmpdump.mplayerhq.hu/).

It's possible you will need ffmpeg too and of course, you will need Totem.

Please let me know if you try it and it breaks...

Installation
------------

1)  Download the code from github.
2)  Place or symlink the get-iplayer directory in:
    * /usr/lib/totem/plugins or equivalent to install for all users.
    * ~/.local/share/totem/plugins or equivalent to install just for yourself.
3)  Open Totem, if "Get iPlayer" does not appear as a possible sidebar, open Edit > Plugins and enable it.
4) Enjoy!

get_iplayer
-----------

This tool allows you to search, index and record/stream TV and radio. Note that the services
connected to by this tool are only available to UK residents. If you want to access any live
streams you should also own a TV License.

While the [original](http://linuxcentre.net/getiplayer) tool is no longer under development,
this plugin works with the continuation of that tool
[here](http://www.infradead.org/get_iplayer/html/get_iplayer.html).
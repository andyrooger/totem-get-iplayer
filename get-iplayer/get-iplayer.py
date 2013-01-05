# -*- coding: utf-8 -*-

import totem
import gobject
import urllib2
import gtk
import threading
from getiplayer_interface import GetIPlayer

IDX_TITLE = 0
IDX_LOADING_NODE = 1
IDX_HAS_LOADED = 2
IDX_PROGRAMME_INDEX = 3

class TreeValues(object):
	def __init__(self, title, loading_node=False, loaded=False, prog_idx=-1):
		self.title = title
		self.loading_node = loading_node
		self.loaded = loaded
		self.prog_idx = prog_idx

	def __internal(self):
		return (self.title, self.loading_node, self.loaded, self.prog_idx)

	def __iter__(self):
		return iter(self.__internal())

	def __len__(self):
		return 4

	def __getitem__(self, key):
		return self.__internal()[key]

class GetIplayerPlugin (totem.Plugin):
	def __init__ (self):
		totem.Plugin.__init__ (self)
		self.totem = None
		self.filter_order = ["type", "version", "channel", "category"]
		self.showing_info = None
		self._mode_callback_id = None

	def activate (self, totem_object):
		# Build the interface
		builder = self.load_interface ("get-iplayer.ui", True, totem_object.get_main_window (), self)
		container = builder.get_object ('getiplayer_pane')

		self.gip = GetIPlayer("/home/andyrooger/git/get_iplayer/get_iplayer")
		progs_store = builder.get_object("getiplayer_progs_store")
		progs_list = builder.get_object("getiplayer_progs_list")
		progs_list.connect("row-expanded", self._row_expanded_cb)
		progs_list.get_selection().connect("changed", self._row_selection_changed_cb)

		self._ui_programme_info = builder.get_object("getiplayer_description_pane")
		self._ui_series = builder.get_object("getiplayer_series_text")
		self._ui_episode = builder.get_object("getiplayer_episode_text")
		self._ui_desc = builder.get_object("getiplayer_desc_text")
		self._ui_thumb = builder.get_object("getiplayer_thumbnail")
		self._ui_mode_list = builder.get_object("getiplayer_modes")
		self._ui_version_list = builder.get_object("getiplayer_versions")
		self._ui_record = builder.get_object("getiplayer_record")
		self._ui_play = builder.get_object("getiplayer_play")

		self._ui_record.connect("clicked", self._record_clicked_cb)
		self._ui_play.connect("clicked", self._play_clicked_cb)

		self.totem = totem_object
		container.show_all ()
		self._ui_programme_info.hide_all()

		# Add the interface to Totem's sidebar
		self.totem.add_sidebar_page ("get-iplayer", _("Get iPlayer"), container)

		self._populate_filter_level(progs_list, None)

	def deactivate (self, totem_object):
		totem_object.remove_sidebar_page ("get-iplayer")

	def _row_expanded_cb(self, tree, iter, path):
		try:
			self._populate_filter_level(tree, iter)
			return
		except ValueError:
			pass # this is not a filtered level of the tree
		if tree.get_model().iter_depth(iter) == len(self.filter_order)-1:
			self._populate_series_and_episodes(tree, iter)

	def _row_selection_changed_cb(self, selection):
		treestore, branch = selection.get_selected()
		index = None if branch is None else treestore.get_value(branch, IDX_PROGRAMME_INDEX)
		self._load_info(None if index == -1 else index)

	def _record_clicked_cb(self, button):
		if self.showing_info is None:
			return
		selected_version = self._ui_version_list.get_active_iter()
		selected_mode = self._ui_mode_list.get_active_iter()
		if selected_version is None or selected_mode is None:
			return
		version = self._ui_version_list.get_model().get_value(selected_version, 0)
		mode = self._ui_mode_list.get_model().get_value(selected_mode, 0)
		def done(out):
			print "Recording Complete"
			print out
			# TODO: Replace this with an update of the recording list
		self.gip.record_programme(self.showing_info, version, mode).on_complete(done)

	def _play_clicked_cb(self, button):
		print "Play is not yet implemented."

	def _version_selected_cb(self, version_list, index, info):
		if self.showing_info != index:
			return
		selected = version_list.get_active_iter()
		if selected is None:
			return
		version = version_list.get_model().get_value(selected, 0)
		for mode, size in (mode.split("=") for mode in info["modesizes"].get(version, "").split(",")):
			self._ui_mode_list.get_model().append([mode, "%s (%s)" % (mode, size)])
		self._ui_mode_list.set_active(0)

	def _filter_at_branch(self, progs_store, branch):
		node_names = []
		while branch is not None:
			node_names.append(progs_store.get_value(branch, IDX_TITLE))
			branch = progs_store.iter_parent(branch)
		return tuple(reversed(node_names))

	def _active_filters(self, progs_store, branch):
		path = self._filter_at_branch(progs_store, branch)
		return dict(zip(self.filter_order, path))

	def _populate_filter_level(self, progs_list, branch):
		progs_store = progs_list.get_model()
		populate_depth = 0 if branch is None else progs_store.iter_depth(branch)+1
		if populate_depth >= len(self.filter_order):
			raise ValueError("This level does not contain filters.")
		
		populate = load_branch(progs_list, branch)
		if populate is None:
			return
		def got_filters(filters):
			populate([(TreeValues(f), []) for f in filters])
		populating = self.filter_order[populate_depth]
		active_filters = self._active_filters(progs_store, branch)
		self.gip.get_filters_and_blanks(populating, **active_filters).on_complete(got_filters)

	def _populate_series_and_episodes(self, progs_list, branch):
		populate = load_branch(progs_list, branch)
		if populate is None:
			return
		def got_episodes(series):
			populate([
				(TreeValues(s), [(TreeValues(ep[1], prog_idx=ep[0]), None) for ep in eps])
				for s, eps in series.iteritems()
			])
		active_filters = self._active_filters(progs_list.get_model(), branch)
		self.gip.get_episodes(**active_filters).on_complete(got_episodes)

	def _load_info(self, index):
		self.showing_info = index

		# First show a loading page
		def prepare_loading():
			if self.showing_info != index:
				return
			self._ui_programme_info.hide_all()
			if index is None:
				return
			self._ui_series.set_text("Loading programme %s..." % index)
			self._ui_episode.set_text("")
			self._ui_desc.set_text("")
			self._ui_thumb.clear()
			if self._mode_callback_id is not None:
				self._ui_version_list.disconnect(self._mode_callback_id)
				self._mode_callback_id = None
			self._ui_mode_list.get_model().clear()
			self._ui_version_list.get_model().clear()
			self._ui_play.set_sensitive(False)
			self._ui_record.set_sensitive(False)
			self._ui_programme_info.show_all()
		gobject.idle_add(prepare_loading)

		if index is None:
			return

		# Then load up the info and populate it when done (if the index has not changed)
		def got_info(info):
			if self.showing_info != index:
				return
			self._ui_series.set_text(info.get("name", "Unknown name"))
			self._ui_episode.set_text(info.get("episode", ""))
			self._ui_desc.set_text(info.get("desc", "No description"))
			self._ui_mode_list.get_model().clear()
			self._ui_version_list.get_model().clear()
			for version in info.get("versions", "").split(","):
				if version:
					self._ui_version_list.get_model().append([version])
			self._mode_callback_id = self._ui_version_list.connect("changed", self._version_selected_cb, index, info)
			self._ui_version_list.set_active(0)
			self._ui_play.set_sensitive(True)
			self._ui_record.set_sensitive(True)

			# Need to load image on another thread
			load_image_in_background(self._ui_thumb, info["thumbnail"],
				cancelcheck=lambda: self.showing_info != index,
				transform=lambda pb: ensure_image_small(pb, 200, 200))
		self.gip.get_programme_info(index).on_complete(lambda info: gobject.idle_add(got_info, info))

def is_branch_loaded(treestore, branch_iter):
	if branch_iter is None:
		return treestore.iter_n_children(branch_iter) > 0
	else:
		return treestore.get_value(branch_iter, IDX_HAS_LOADED)
	

def is_branch_loading(treestore, branch_iter):
	if treestore.iter_n_children(branch_iter) != 1:
		return False
	return treestore.get_value(treestore.iter_children(branch_iter), IDX_LOADING_NODE)

def load_branch(tree, branch_iter, force=False):
	'''Start loading a branch, return either a function to call on load completion or None if it is already loading.'''
	treestore = tree.get_model()
	if not force and (is_branch_loaded(treestore, branch_iter) or is_branch_loading(treestore, branch_iter)):
		return None

	branch_path = None if branch_iter is None else treestore.get_path(branch_iter)

	def start_load():
		branch_iter = None if branch_path is None else treestore.get_iter(branch_path)
		expansion_state = None if branch_path is None else tree.row_expanded(branch_path)

		child = treestore.iter_children(branch_iter)
		while child:
			treestore.remove(child)
			child = treestore.iter_children(branch_iter)
		treestore.append(branch_iter, TreeValues("Loading...", loading_node=True))
		if expansion_state is not None:
			if expansion_state: tree.expand_row(branch_path, False)
			else: tree.collapse_row(branch_path)
	gobject.idle_add(start_load)

	def populate(children):
		'''
		Takes a list of children to add. Each child is a tuple of (tree values for child, subchildren).
		Sub children are childrens children. These should be None if the node should not be expandable, or [..] if it should be.
		Subchildren  has the same format of children if it exists.
		'''
		branch_iter = None if branch_path is None else treestore.get_iter(branch_path)
		expansion_state = None if branch_path is None else tree.row_expanded(branch_path)

		if not is_branch_loading(treestore, branch_iter):
			# Should only populate if a load is supposed to be in progress currently
			# Force means we can populate when a load is complete
			allowed_by_force = force and is_branch_loaded(treestore, branch_iter)
			if not allowed_by_force:
				return
		if branch_iter is not None:
			treestore.set_value(branch_iter, IDX_HAS_LOADED, True)

		# Remove ALL children (in case we used force)
		child = treestore.iter_children(branch_iter)
		while child:
			treestore.remove(child)
			child = treestore.iter_children(branch_iter)
		def add_children(child_list, branch):
			for c, subc in child_list:
				c_iter = treestore.append(branch, c)
				if subc == []:
					treestore.append(c_iter, TreeValues("Nothing"))
				elif subc is not None:
					add_children(subc, c_iter)
		add_children(children, branch_iter)

		if expansion_state is not None:
			if expansion_state: tree.expand_row(branch_path, False)
			else: tree.collapse_row(branch_path)
		return False
	return lambda children: gobject.idle_add(populate, children)

def load_image_in_background(image, imageurl, cancelcheck=None, transform=None):
	def on_complete(pb):
		if cancelcheck is None or cancelcheck():
			return
		image.set_from_pixbuf(pb)

	def load_image():
		pb = None
		try:
			response = urllib2.urlopen(imageurl)
			loader = gtk.gdk.PixbufLoader()
			loader.write(response.read())
			loader.close()
			pb = loader.get_pixbuf()
		except:
			pass
		if transform is not None:
			pb = transform(pb)
		gobject.idle_add(on_complete, pb)
	threading.Thread(target=load_image).start()

def ensure_image_small(pb, max_width, max_height):
	width = pb.get_width()
	height = pb.get_height()
	resize = False
	if width > max_width:
		resize = True
		height *= float(max_width)/float(width)
		height = int(height)
		width = max_width
	if height > max_height:
		resize = True
		width *= float(max_height)/float(height)
		width = int(width)
		height = max_height
	if resize:
		return pb.scale_simple(width, height, gtk.gdk.INTERP_BILINEAR)
	else:
		return pb

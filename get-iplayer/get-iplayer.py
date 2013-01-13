# -*- coding: utf-8 -*-

# totem-get-iplayer
# Copyright (C) 2013  Andy Gurden
# 
#     This file is part of totem-get-iplayer.
# 
#     totem-get-iplayer is free software: you can redistribute it and/or modify
#     it under the terms of the GNU General Public License as published by
#     the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.
# 
#     totem-get-iplayer is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU General Public License for more details.
# 
#     You should have received a copy of the GNU General Public License
#     along with totem-get-iplayer.  If not, see <http://www.gnu.org/licenses/>.

import totem
import gobject
import urllib2
import gtk
import gconf
import threading
import subprocess
from collections import defaultdict
from getiplayer_interface import GetIPlayer

IDX_TITLE = 0
IDX_DISPLAY = 1
IDX_LOADING_NODE = 2
IDX_HAS_LOADED = 3
IDX_PROGRAMME_INDEX = 4

IDXH_INDEX = 0
IDXH_NAME = 1
IDXH_VERSION = 2
IDXH_MODE = 3
IDXH_LOCATION = 4

GCONF_KEY = "/apps/totem/plugins/get-iplayer"

AVAILABLE_FILTERS = ["channel", "category", "type", "version"]
DEFAULT_FILTERS = ["type", "channel", "category"]

class TreeValues(object):
	def __init__(self, title, loading_node=False, loaded=False, prog_idx=-1, info_type=None):
		self.title = title
		self.display = title or "(No %s)" % ((info_type or "Name").title(),)
		self.loading_node = loading_node
		self.loaded = loaded
		self.prog_idx = prog_idx

	def __internal(self):
		return (self.title, self.display, self.loading_node, self.loaded, self.prog_idx)

	def __iter__(self):
		return iter(self.__internal())

	def __len__(self):
		return 5

	def __getitem__(self, key):
		return self.__internal()[key]

class GetIplayerPlugin (totem.Plugin):
	def __init__ (self):
		totem.Plugin.__init__ (self)
		self.totem = None
		self.showing_info = None
		self._mode_callback_id = None
		self.has_sidebar = False

	def activate (self, totem_object):
		# Build the interface
		builder = self.load_interface ("get-iplayer.ui", True, totem_object.get_main_window (), self)
		self._ui_container = builder.get_object ('getiplayer_top_pane')
		self._ui_container.show_all ()

		# Sidebar
		progs_store = builder.get_object("getiplayer_progs_store")
		self._ui_progs_list = builder.get_object("getiplayer_progs_list")
		self._ui_progs_list.connect("row-expanded", self._row_expanded_cb)
		self._ui_progs_list.get_selection().connect("changed", self._row_selection_changed_cb)

		self._ui_programme_info = builder.get_object("getiplayer_description_pane")
		self._ui_series = builder.get_object("getiplayer_series_text")
		self._ui_episode = builder.get_object("getiplayer_episode_text")
		self._ui_duration = builder.get_object("getiplayer_duration_text")
		self._ui_desc = builder.get_object("getiplayer_desc_text")
		self._ui_thumb = builder.get_object("getiplayer_thumbnail")
		self._ui_mode_list = builder.get_object("getiplayer_modes")
		self._ui_version_list = builder.get_object("getiplayer_versions")
		self._ui_record = builder.get_object("getiplayer_record")
		self._ui_play = builder.get_object("getiplayer_play")
		self._ui_history_list = builder.get_object("getiplayer_history")
		self._ui_history_pane = builder.get_object("getiplayer_history_scroll")

		self._ui_record.connect("clicked", self._record_clicked_cb)
		self._ui_play.connect("clicked", self._play_clicked_cb)
		self._ui_history_list.connect("row-activated", self._history_activated_cb)

		self.config = Configuration(builder, self.attach_getiplayer)
		self.totem = totem_object

		self.attach_getiplayer()

	def deactivate (self, totem_object):
		totem_object.remove_sidebar_page ("get-iplayer")

	def attach_getiplayer(self):
		location_correct = False
		loc = self.config.config_getiplayer_location or which("get-iplayer")
		if loc is not None:
			flvstreamerloc = self.config.config_flvstreamer_location or which("flvstreamer")
			ffmpegloc = self.config.config_ffmpeg_location or which("ffmpeg")
			try:
				self.gip = GetIPlayer(loc, flvstreamerloc, ffmpegloc)
			except OSError: pass
			else:
				location_correct = True

		# Add the interface to Totem's sidebar only if get_iplayer is accessible, otherwise show error
		if not location_correct:
			self.totem.action_error("Get iPlayer", "Cannot find get_iplayer. Please install it if you need to and set the location under plugin configuration.")
		self.reset_ui(location_correct)
		return location_correct

	def reset_ui(self, iplayer_attached):
		if iplayer_attached:
			if not self.has_sidebar:
				self.totem.add_sidebar_page ("get-iplayer", _("Get iPlayer"), self._ui_container)
				self.has_sidebar = True
			self._ui_programme_info.hide_all()
			self._ui_history_pane.hide_all()
			self._ui_progs_list.get_model().clear()
			self._populate_filter_level(self._ui_progs_list, None)
			self._populate_history()
		else:
			if self.has_sidebar:
				self.totem.remove_sidebar_page("get-iplayer")
				self.has_sidebar = False

	def create_configure_dialog(self, *args):
		return self.config.create_configure_dialog(args)

	def _row_expanded_cb(self, tree, iter, path):
		try:
			self._populate_filter_level(tree, iter)
			return
		except ValueError:
			pass # this is not a filtered level of the tree
		if tree.get_model().iter_depth(iter) == len(self.config.config_filter_order)-1:
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
		self.gip.record_programme(self.showing_info, None, version, mode).on_complete(lambda _: self._populate_history())
		self._populate_history()

	def _play_clicked_cb(self, button):
		if self.showing_info is None:
			return
		selected_version = self._ui_version_list.get_active_iter()
		selected_mode = self._ui_mode_list.get_active_iter()
		if selected_version is None or selected_mode is None:
			return
		version = self._ui_version_list.get_model().get_value(selected_version, 0)
		mode = self._ui_mode_list.get_model().get_value(selected_mode, 0)
		name = self._ui_series.get_text() + " - " + self._ui_episode.get_text()
		fd = self.gip.stream_programme_to_pipe(self.showing_info, version, mode)
		self.totem.add_to_playlist_and_play("fd://%s" % fd, name, False)

	def _version_selected_cb(self, version_list, index, info):
		if self.showing_info != index:
			return
		selected = version_list.get_active_iter()
		if selected is None:
			return
		version = version_list.get_model().get_value(selected, 0)
		self._ui_mode_list.get_model().clear()
		modesizes = []
		modes = info.get("modesizes", {}).get(version, "")
		if modes:
			modesizes = [mode.split("=") for mode in modes.split(",")]
		else:
			modes = info.get("modes", {}).get(version, "")
			if modes:
				modesizes = [(mode, None) for mode in modes.split(",")]
		modesizes.insert(0, ("best", None))
		for mode, size in modesizes:
			display = mode if size is None else "%s (%s)" % (mode, size)
			self._ui_mode_list.get_model().append([mode, display])
		self._ui_mode_list.set_active(0)

	def _history_activated_cb(self, treeview, path, column):
		treemodel = treeview.get_model()
		iter = treemodel.get_iter(path)
		file = treemodel.get_value(iter, IDXH_LOCATION)
		if not file:
			return
		episode = treemodel.get_value(iter, IDXH_NAME)
		series = treemodel.get_value(treemodel.iter_parent(iter), IDXH_NAME)
		name = series + " - " + episode
		self.totem.add_to_playlist_and_play("file://" + file, name, True)

	def _filter_at_branch(self, progs_store, branch):
		node_names = []
		while branch is not None:
			node_names.append(progs_store.get_value(branch, IDX_TITLE))
			branch = progs_store.iter_parent(branch)
		return tuple(reversed(node_names))

	def _active_filters(self, progs_store, branch):
		path = self._filter_at_branch(progs_store, branch)
		return dict(zip(self.config.config_filter_order, path))

	def _populate_filter_level(self, progs_list, branch):
		progs_store = progs_list.get_model()
		populate_depth = 0 if branch is None else progs_store.iter_depth(branch)+1
		if populate_depth >= len(self.config.config_filter_order):
			raise ValueError("This level does not contain filters.")
		
		populating = self.config.config_filter_order[populate_depth]

		populate = load_branch(progs_list, branch)
		if populate is None:
			return
		def got_filters(filters):
			populate([(TreeValues(f, info_type=populating), []) for f in filters])
		active_filters = self._active_filters(progs_store, branch)
		self.gip.get_filters_and_blanks(populating, **active_filters).on_complete(got_filters)

	def _populate_series_and_episodes(self, progs_list, branch):
		populate = load_branch(progs_list, branch)
		if populate is None:
			return
		def got_episodes(series):
			populate([
				(TreeValues(s, info_type="series"), [(TreeValues(ep[1], prog_idx=ep[0], info_type="episode"), None) for ep in eps])
				for s, eps in series.iteritems()
			])
		active_filters = self._active_filters(progs_list.get_model(), branch)
		self.gip.get_episodes(**active_filters).on_complete(got_episodes)

	def _populate_history(self):
		def populate_store(history):
			self._ui_history_pane.hide_all()
			historystore = self._ui_history_list.get_model()
			historystore.clear()
			if self.gip.recordings.values():
				recording_branch = historystore.append(None, [-1, "Currently Recording", "", "", ""])
				for name, version, mode in self.gip.recordings.values():
					historystore.append(recording_branch, [-1, name + " (Recording...)", version, mode, ""])
			by_series = defaultdict(list)
			for index, series, episode, version, mode, location in history:
				by_series[series].append((index, episode, version, mode, location))
			for series, episodes in by_series.iteritems():
				series_branch = historystore.append(None, [-1, series, "", "", ""])
				for index, episode, version, mode, location in episodes:
					historystore.append(series_branch, [index, episode, version, mode, location])
			if historystore.get_iter_root() is not None:
				self._ui_history_pane.show_all()

		self.gip.get_history().on_complete(lambda history: gobject.idle_add(populate_store, history))

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
			self._ui_duration.set_text("")
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
			duration = info.get("duration", "Unknown")
			hasduration = False
			try:
				duration = int(duration) # seconds
				duration = str(duration // 60) + " minutes"
				hasduration = True
			except ValueError:
				# Wasn't a number, try mins:seconds format
				try:
					minutes, seconds = duration.split(":")
					minutes = int(minutes)
					seconds = int(seconds)
					if seconds >= 30:
						minutes += 1
					duration = str(minutes) + " minutes"
					hasduration = True
				except ValueError:
					pass # Leave as it is
			self._ui_duration.set_text(duration)
			self._ui_desc.set_text(info.get("desc", "No description"))
			self._ui_mode_list.get_model().clear()
			self._ui_version_list.get_model().clear()
			for version in info.get("versions", "").split(","):
				if version:
					self._ui_version_list.get_model().append([version])
			self._mode_callback_id = self._ui_version_list.connect("changed", self._version_selected_cb, index, info)
			self._ui_version_list.set_active(0)
			self._ui_play.set_sensitive(True)
			if hasduration:
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

def which(program):
	'''Finds a program's location based on its name.'''
	try:
		return subprocess.check_output(["which", program], stderr=subprocess.STDOUT).strip()
	except subprocess.CalledProcessError:
		return None

class Configuration(object):
	'''All config-related business.'''

	def __init__(self, builder, onconfigchanged):
		self.gconf = gconf.client_get_default()
		self.onconfigchanged = onconfigchanged

		self.config_dialog = builder.get_object('config_dialog')
		builder.get_object('config_container').show_all()

		builder.get_object("config_cancel_button").connect("clicked", (lambda button: self.config_dialog.hide()))
		builder.get_object("config_ok_button").connect("clicked", self._config_confirmed_cb)

		self._uiconfig_getiplayer_location = builder.get_object("config_getiplayer_loc")
		self._uiconfig_getiplayer_location.add_filter(self.__get_filter("get_iplayer", "get_iplayer"))
		self._uiconfig_getiplayer_guess = builder.get_object("config_getiplayer_locdefault")
		self._uiconfig_getiplayer_guess.connect("toggled",
			lambda button: self._intelligent_guess_clicked_cb(self._uiconfig_getiplayer_location, button))

		self._uiconfig_flvstreamer_location = builder.get_object("config_flvstreamer_loc")
		self._uiconfig_flvstreamer_location.add_filter(self.__get_filter("flvstreamer", "flvstreamer"))
		self._uiconfig_flvstreamer_guess = builder.get_object("config_flvstreamer_locdefault")
		self._uiconfig_flvstreamer_guess.connect("toggled",
			lambda button: self._intelligent_guess_clicked_cb(self._uiconfig_flvstreamer_location, button))

		self._uiconfig_ffmpeg_location = builder.get_object("config_ffmpeg_loc")
		self._uiconfig_ffmpeg_location.add_filter(self.__get_filter("ffmpeg", "ffmpeg"))
		self._uiconfig_ffmpeg_guess = builder.get_object("config_ffmpeg_locdefault")
		self._uiconfig_ffmpeg_guess.connect("toggled",
			lambda button: self._intelligent_guess_clicked_cb(self._uiconfig_ffmpeg_location, button))

		self._uiconfig_filters_available = builder.get_object("config_filters_available")
		self._uiconfig_filters_used = builder.get_object("config_filters_used")

		filter_target = [("FILTER_DRAG", gtk.TARGET_SAME_APP, 0)]
		self._uiconfig_filters_available.enable_model_drag_source(gtk.gdk.BUTTON1_MASK, filter_target, gtk.gdk.ACTION_MOVE)
		self._uiconfig_filters_available.enable_model_drag_dest(filter_target, gtk.gdk.ACTION_MOVE)
		self._uiconfig_filters_available.connect("drag-data-received", self._filter_move_cb)
		self._uiconfig_filters_available.connect("drag-data-get", self._filter_getdata_cb)
		self._uiconfig_filters_used.enable_model_drag_source(gtk.gdk.BUTTON1_MASK, filter_target, gtk.gdk.ACTION_MOVE)
		self._uiconfig_filters_used.enable_model_drag_dest(filter_target, gtk.gdk.ACTION_MOVE)
		self._uiconfig_filters_used.connect("drag-data-received", self._filter_move_cb)
		self._uiconfig_filters_used.connect("drag-data-get", self._filter_getdata_cb)

	def create_configure_dialog(self, *args):
		self._init_ui(self.config_getiplayer_location, self._uiconfig_getiplayer_location, self._uiconfig_getiplayer_guess)
		self._init_ui(self.config_flvstreamer_location, self._uiconfig_flvstreamer_location, self._uiconfig_flvstreamer_guess)
		self._init_ui(self.config_ffmpeg_location, self._uiconfig_ffmpeg_location, self._uiconfig_ffmpeg_guess)

		configured_filter_order = self.config_filter_order
		available_filter_store = self._uiconfig_filters_available.get_model()
		used_filter_store = self._uiconfig_filters_used.get_model()
		available_filter_store.clear()
		used_filter_store.clear()
		for filter in AVAILABLE_FILTERS:
			if filter not in configured_filter_order:
				available_filter_store.append([filter])
		for filter in configured_filter_order:
			used_filter_store.append([filter])

		self.config_dialog.set_default_response(gtk.RESPONSE_OK)
		return self.config_dialog

	def _init_ui(self, loc, uilocation, uiguess):
		has_loc = loc is not None
		uilocation.set_sensitive(has_loc)
		uiguess.set_active(not has_loc)
		if has_loc:
			uilocation.set_filename(loc)
		else:
			uilocation.unselect_all()

	def __get_filter(self, name, exename):
		filter = gtk.FileFilter()
		filter.set_name(name + " executable")
		filter.add_pattern(exename)
		return filter

	def _filter_getdata_cb(self, tree, ctx, selection, info, timestamp):
		model, iter = tree.get_selection().get_selected()
		if tree is self._uiconfig_filters_used and tree.get_model().iter_n_children(None) <= 1:
			return # Always need at least one filter used
		text = model.get_value(iter, 0)
		selection.set("FILTER_DRAG", 8, text)
		model.remove(iter)

	def _filter_move_cb(self, tree, ctx, x, y, selection, info, timestamp):
		drop_info = tree.get_dest_row_at_pos(x, y)
		model = tree.get_model()
		data = selection.data
		if data is None:
			return
		if drop_info:
			path, position = drop_info
			iter = model.get_iter(path)
			if position == gtk.TREE_VIEW_DROP_BEFORE or position == gtk.TREE_VIEW_DROP_INTO_OR_BEFORE:
				model.insert_before(iter, [data])
			else:
				model.insert_after(iter, [data])
		else:
			model.append([data])

	def _intelligent_guess_clicked_cb(self, location_box, button):
		location_box.set_sensitive(not button.get_active())

	def _config_confirmed_cb(self, button):
		gip = self._uiconfig_getiplayer_location.get_filename()
		if gip is None or self._uiconfig_getiplayer_guess.get_active():
			del self.config_getiplayer_location
		else:
			self.config_getiplayer_location = gip

		gip = self._uiconfig_flvstreamer_location.get_filename()
		if gip is None or self._uiconfig_flvstreamer_guess.get_active():
			del self.config_flvstreamer_location
		else:
			self.config_flvstreamer_location = gip

		gip = self._uiconfig_ffmpeg_location.get_filename()
		if gip is None or self._uiconfig_ffmpeg_guess.get_active():
			del self.config_ffmpeg_location
		else:
			self.config_ffmpeg_location = gip

		# Commented as it currently results in a message box appearing in the background and locking the UI
		#if gip is None and not self._uiconfig_getiplayer_guess.get_active():
		#	dlg = gtk.MessageDialog(
		#		parent=self.config_dialog,
		#		flags=gtk.DIALOG_MODAL|gtk.DIALOG_DESTROY_WITH_PARENT,
		#		buttons=gtk.BUTTONS_OK,
		#		message_format="You have not selected the location of get_iplayer. Intelligent guessing has been automatically enabled."
		#	)
		#	dlg.run()
		#	dlg.destroy()

		self.config_filter_order = [row[0] for row in self._uiconfig_filters_used.get_model()]

		self.onconfigchanged()
		self.config_dialog.hide()

	@property
	def config_getiplayer_location(self):
		return self.gconf.get_string(GCONF_KEY + "/getiplayer_location")

	@config_getiplayer_location.setter
	def config_getiplayer_location(self, value):
		self.gconf.set_string(GCONF_KEY + "/getiplayer_location", value)

	@config_getiplayer_location.deleter
	def config_getiplayer_location(self):
		self.gconf.unset(GCONF_KEY + "/getiplayer_location")

	@property
	def config_flvstreamer_location(self):
		return self.gconf.get_string(GCONF_KEY + "/flvstreamer_location")

	@config_flvstreamer_location.setter
	def config_flvstreamer_location(self, value):
		self.gconf.set_string(GCONF_KEY + "/flvstreamer_location", value)

	@config_flvstreamer_location.deleter
	def config_flvstreamer_location(self):
		self.gconf.unset(GCONF_KEY + "/flvstreamer_location")

	@property
	def config_ffmpeg_location(self):
		return self.gconf.get_string(GCONF_KEY + "/ffmpeg_location")

	@config_ffmpeg_location.setter
	def config_ffmpeg_location(self, value):
		self.gconf.set_string(GCONF_KEY + "/ffmpeg_location", value)

	@config_ffmpeg_location.deleter
	def config_ffmpeg_location(self):
		self.gconf.unset(GCONF_KEY + "/ffmpeg_location")

	@property
	def config_filter_order(self):
		filters = self.gconf.get_list(GCONF_KEY + "/active_filters", gconf.VALUE_STRING)
		return DEFAULT_FILTERS if filters == [] else filters

	@config_filter_order.setter
	def config_filter_order(self, value):
		self.gconf.set_list(GCONF_KEY + "/active_filters", gconf.VALUE_STRING, value)

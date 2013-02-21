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
import re
import os
from collections import defaultdict
from getiplayer_interface import GetIPlayer, combine_modes

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
		self.current_search = None
		self._current_search_input = None
		self.gip = None
		self._is_starting_stream = False # Used when a file is closed to figure out when to avoid killing a stream

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
		self._ui_progs_list.connect("row-activated", self._row_activated_cb)
		self._ui_progs_refresh = builder.get_object("getiplayer_progs_refresh")
		self._ui_progs_refresh.connect("clicked", self._refresh_clicked_cb)

		self._ui_search_entry = builder.get_object("getiplayer_search_entry")
		self._ui_search_entry.set_tooltip_text("Current Search: None")
		self._ui_search_clear = builder.get_object("getiplayer_search_clear")
		self._ui_search_search = builder.get_object("getiplayer_search_search")
		self._ui_programme_info = builder.get_object("getiplayer_description_pane")
		self._ui_series = builder.get_object("getiplayer_series_text")
		self._ui_episode = builder.get_object("getiplayer_episode_text")
		self._ui_duration = builder.get_object("getiplayer_duration_text")
		self._ui_expiry = builder.get_object("getiplayer_expiry_text")
		self._ui_desc = builder.get_object("getiplayer_desc_text")
		self._ui_thumb = builder.get_object("getiplayer_thumbnail")
		self._ui_mode_list = builder.get_object("getiplayer_modes")
		self._ui_version_list = builder.get_object("getiplayer_versions")
		self._ui_record = builder.get_object("getiplayer_record")
		self._ui_play = builder.get_object("getiplayer_play")
		self._ui_history_list = builder.get_object("getiplayer_history")
		self._ui_history_pane = builder.get_object("getiplayer_history_scroll")

		self._ui_search_entry.connect("changed", self._search_changed_cb)
		self._ui_search_entry.connect("activate", self._search_activated_cb)
		self._ui_search_clear.connect("clicked", self._search_clear_clicked_cb)
		self._ui_search_search.connect("clicked", self._search_clicked_cb)
		self._ui_record.connect("clicked", self._record_clicked_cb)
		self._ui_play.connect("clicked", self._play_clicked_cb)
		self._ui_history_list.connect("row-activated", self._history_activated_cb)
		self._ui_history_list.connect("key-press-event", self._history_keypress_cb)
		self._ui_mode_list.connect("changed", self._mode_selected_cb)

		self.config = Configuration(builder, self.attach_getiplayer)
		self.totem = totem_object

		self.totem.connect("file-closed", self._file_closed_cb)

		self.attach_getiplayer()

	def deactivate (self, totem_object):
		totem_object.remove_sidebar_page ("get-iplayer")
		self.has_sidebar = False
		if self.gip is not None:
			self.gip.close()

	def attach_getiplayer(self):
		location_correct = False
		loc = self.config.config_getiplayer_location or which("get-iplayer")
		if loc is not None:
			flvstreamerloc = self.config.config_flvstreamer_location or which("rtmpdump") or which("flvstreamer")
			ffmpegloc = self.config.config_ffmpeg_location or which("ffmpeg")
			localfiles_dirs = self.config.config_localfiles_directories
			if self.gip is not None:
				self.gip.close()
			try:
				self.gip = GetIPlayer(loc, flvstreamerloc, ffmpegloc, localfiles_dirs)
			except OSError: pass
			else:
				location_correct = True

		# Add the interface to Totem's sidebar only if get_iplayer is accessible, otherwise show error
		if not location_correct:
			self.totem.action_error("Get iPlayer", "Cannot find get_iplayer. Please install it if you need to and set the location under plugin configuration.")
		self.reset_ui(location_correct)
		return location_correct

	def _reset_progtree(self):
		if self.has_sidebar:
			self._ui_progs_list.get_model().clear()
			self._populate_filter_level(self._ui_progs_list, None)

	def reset_ui(self, iplayer_attached):
		if iplayer_attached:
			if not self.has_sidebar:
				self.totem.add_sidebar_page ("get-iplayer", _("Get iPlayer"), self._ui_container)
				self.has_sidebar = True
			self._search_clear_clicked_cb(self._ui_search_clear)
			self._ui_programme_info.hide_all()
			self._ui_history_pane.hide_all()
			self._reset_progtree()
			self._populate_history()
		else:
			if self.has_sidebar:
				self.totem.remove_sidebar_page("get-iplayer")
				self.has_sidebar = False

	def create_configure_dialog(self, *args):
		return self.config.create_configure_dialog(args)

	def _search_changed_cb(self, entry):
		has_text = bool(entry.get_text())
		self._ui_search_clear.set_sensitive(has_text)
		self._ui_search_search.set_sensitive(has_text)

	def _search_activated_cb(self, entry):
		self._ui_search_search.clicked()

	def _search_clear_clicked_cb(self, button):
		self._ui_search_entry.set_text("")
		self._search_clicked_cb(button)

	def _search_clicked_cb(self, button):
		self._ui_search_search.set_sensitive(False)
		old_search = self._current_search_input
		self._current_search_input = self._ui_search_entry.get_text() or None
		self.current_search = None if self._current_search_input is None else self._convert_search_terms(self._current_search_input)
		if old_search != self._current_search_input:
			self._ui_search_entry.set_tooltip_text("Current Search: %s" % (self._current_search_input,))
			self._reset_progtree()

	def _row_expanded_cb(self, tree, iter, path):
		try:
			self._populate_filter_level(tree, iter)
		except ValueError:
			# this is not a filtered level of the tree
			if tree.get_model().iter_depth(iter) == len(self.config.config_filter_order)-1:
				self._populate_series_and_episodes(tree, iter)

		# Try to expand so long as there is only one child
		open_iter = iter
		treemodel = tree.get_model()
		while open_iter is not None:
			if treemodel.iter_n_children(open_iter) == 1:
				tree.expand_row(treemodel.get_path(open_iter), False)
				open_iter = treemodel.iter_children(open_iter)
			else:
				open_iter = None

	def _row_selection_changed_cb(self, selection):
		treestore, branch = selection.get_selected()
		index = None if branch is None else treestore.get_value(branch, IDX_PROGRAMME_INDEX)
		self._load_info(None if index == -1 else index)

	def _row_activated_cb(self, tree, path, column):
		row_iter = tree.get_model().get_iter(path)
		index = tree.get_model().get_value(row_iter, IDX_PROGRAMME_INDEX)
		if index != -1:
			self.play_programme(index)

	def _refresh_clicked_cb(self, button):
		self._ui_container.set_sensitive(False)
		oldbuttontt = button.get_tooltip_text()
		button.set_tooltip_text("Refreshing...")
		def refresh_complete():
			button.set_tooltip_text(oldbuttontt)
			self._ui_container.set_sensitive(True)
			self.reset_ui(True)
		self.gip.refresh_cache(False).on_complete(lambda _: gobject.idle_add(refresh_complete), self.show_errors("refreshing"))

	def _record_clicked_cb(self, button):
		if self.showing_info is None:
			return
		selected_version = self._ui_version_list.get_active_iter()
		selected_mode = self._ui_mode_list.get_active_iter()
		if selected_version is None or selected_mode is None:
			return
		version = self._ui_version_list.get_model().get_value(selected_version, 0)
		mode = self._ui_mode_list.get_model().get_value(selected_mode, 0)
		if not mode:
			return # Loading
		def on_recording_fail(errs):
			self.show_errors("recording")(errs)
			self._populate_history()
		self.gip.record_programme(
			self.showing_info,
			None,
			version,
			mode
		).on_complete(
			lambda _: self._populate_history(),
			on_recording_fail
		)
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
		if not mode:
			return # Loading
		name = self._ui_series.get_text() + " - " + self._ui_episode.get_text()
		self.play_programme(self.showing_info, name=name, version=version, mode=mode)

	def play_programme(self, index, name=None, version=None, mode=None):
		'''
		Give this the correct information and it will play a programme.
		With the wrong information it will try and fail somewhere in the streaming.
		Give no information and it will work it out if it can...
		'''
		if name is None or version is None:
			def got_info(info):
				newname = name if name is not None else "%s - %s" % (info.get("name", "Unknown name"), info.get("episode", ""))
				if version is None and self.config.config_preferred_version not in info.get("versions", "").split(","):
					self.totem.action_error("Get iPlayer", "Preferred version not found for programme %s." % index)
					return
				newversion = version if version is not None else self.config.config_preferred_version
				self.play_programme(index, name=newname, version=newversion, mode=mode)
			self.gip.get_programme_info(index).on_complete(got_info, self.show_errors("trying to play programme"))
			return
		if mode is None:
			def got_streams(streams):
				mode_and_bitrate = [(mode, int(stream["bitrate"])) for mode, stream in streams.iteritems() if stream["bitrate"]]
				if not mode_and_bitrate:
					return
				within_acceptable = filter(lambda mb: mb[1] <= self.config.config_preferred_bitrate, mode_and_bitrate)
				newmode = min(within_acceptable if within_acceptable else mode_and_bitrate, key=lambda mb: (mb[1], mb[0]))[0]
				self.play_programme(index, name=name, version=version, mode=newmode)
			self.gip.get_stream_info(index, version).on_complete(got_streams, self.show_errors("trying find programme streams"))
			return

		self._is_starting_stream = True # Next file close will not kill the main stream
		fd, streamresult = self.gip.stream_programme_to_pipe(index, version, mode)
		streamresult.on_complete(onerror=self.show_errors("playing programme"))
		gobject.idle_add(self.totem.add_to_playlist_and_play, "fd://%s" % fd, name, False)


	def _version_selected_cb(self, version_list, index, info):
		if self.showing_info != index:
			return
		selected = version_list.get_active_iter()
		if selected is None:
			return
		version = version_list.get_model().get_value(selected, 0)
		self._ui_mode_list.set_sensitive(False)
		self._ui_mode_list.get_model().clear()
		self._ui_mode_list.get_model().append(["", "Loading..."])
		self._ui_mode_list.set_active(0)

		def got_modes(modes, version):
			version_selected = version_list.get_active_iter()
			if version_selected is None or version_list.get_model().get_value(version_selected, 0) != version or self.showing_info != index:
				return
			self._ui_mode_list.get_model().clear()
			active_mode_iter = None # Will be the closest under or equal to preferred bitrate
			lowest_mode_iter = None # Used rather than mode_iter at the end in case there were no modes
			moderates = combine_modes((mode, int(modeinfo["bitrate"]) if modeinfo.get("bitrate", "") else 0) for mode, modeinfo in modes.iteritems())
			for mode, bitrate in sorted(moderates.iteritems(), key=lambda m:(-m[1], m[0])):
				display = "%s (%skbps)" % (mode, bitrate) if bitrate else mode
				mode_iter = self._ui_mode_list.get_model().append([mode, display])
				# Only assign first when we get to correct bitrate, and ignore 0 bitrate streams
				if bitrate != 0:
					if active_mode_iter is None and bitrate <= self.config.config_preferred_bitrate:
						active_mode_iter = mode_iter # bitrates decreasing, so we want first one after we pass (or equal) the preference
					lowest_mode_iter = mode_iter
			if active_mode_iter is None:
				active_mode_iter = lowest_mode_iter
			if active_mode_iter is not None:
				self._ui_mode_list.set_active_iter(active_mode_iter)
			else:
				self._ui_mode_list.set_active(0)
			self._ui_mode_list.set_sensitive(True)

		self.gip.get_stream_info(index, version).on_complete(lambda modes: gobject.idle_add(got_modes, modes, version), self.show_errors("retrieving modes"))

	def _mode_selected_cb(self, mode_list):
		mode_iter = mode_list.get_active_iter()
		selected_mode = None if mode_iter is None else mode_list.get_model().get_value(mode_iter, 0)
		islive = self._ui_episode.get_text() == "live"
		self._ui_play.set_sensitive(bool(selected_mode)) # Enabled if we have a valid mode
		self._ui_record.set_sensitive(bool(selected_mode) and not islive)


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

	def _history_keypress_cb(self, treeview, event):
		if "Delete" != gtk.gdk.keyval_name(event.keyval):
			return False

		treemodel, treeiter = treeview.get_selection().get_selected()
		if treeiter is None:
			return True
		name = treemodel.get_value(treeiter, IDXH_NAME)
		file = treemodel.get_value(treeiter, IDXH_LOCATION)
		wholeseries = not file

		dlg = gtk.MessageDialog(
			parent=self.totem.get_main_window(),
			flags=gtk.DIALOG_MODAL|gtk.DIALOG_DESTROY_WITH_PARENT,
			type=gtk.MESSAGE_WARNING,
			buttons=gtk.BUTTONS_OK_CANCEL,
			message_format="You are about to delete the %s; %s. Are you sure you want to do this?" % (
				"entire series" if wholeseries else "programme",
				name
			)
		)
		response = dlg.run()
		dlg.destroy()

		if response != gtk.RESPONSE_OK:
			return True

		if wholeseries:
			childiter = treemodel.iter_children(treeiter)
			while childiter is not None:
				os.remove(treemodel.get_value(childiter, IDXH_LOCATION))
				childiter = treemodel.iter_next(childiter)
		else:
			os.remove(file)
		self._populate_history()

		return True

	def _file_closed_cb(self, totem):
		if not self._is_starting_stream:
			self.gip.close_main_stream()
		self._is_starting_stream = False

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
		self.gip.get_filters_and_blanks(
			populating,
			self.current_search,
			**active_filters
		).on_complete(
			got_filters,
			self.show_errors_and_cancel_populate(populate, populating)
		)

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
		self.gip.get_episodes(
			self.current_search,
			**active_filters
		).on_complete(
			got_episodes,
			self.show_errors_and_cancel_populate(populate, "programme")
		)

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

		self.gip.get_history().on_complete(lambda history: gobject.idle_add(populate_store, history), self.show_errors("retrieving recordings"))

	def _convert_search_terms(self, terms):
		st = self.config.config_search_type
		if st == "word":
			words = terms.split()
			return ''.join(r"(?=.*(\W|\b)" + re.escape(word) + r"(\b|\W))" for word in words)
		elif st == "wildcard":
			# Should be split up by words, no particular order for words
			# Normal word should search for whole word
			# Single * can be ignored, searches for anything or nothing
			# Word with * can be a single word with anything where the * is - e.g. garden*=gardening or garden etc
			words = terms.split()
			regex_words = []
			for word in words:
				if re.search(r"[^\*]+", word) is not None: # not just ****
					word = re.escape(word)
					regex_words.append(word.replace(r"\*", r"\w*"))
			return ''.join(r"(?=.*(\b|\W)" + word + r"(\W|\b))" for word in regex_words)
		elif st == "regex":
			# Exact search with regex
			return terms
		else:
			return terms # On error use regex...

	def _load_info(self, index):
		'''Loads information for a particular programme.'''
		self.showing_info = index

		# First show a loading page
		def prepare_loading():
			if self.showing_info != index:
				return
			self._ui_programme_info.hide_all()
			self._ui_programme_info.set_sensitive(False)
			if index is None:
				return
			self._ui_series.set_text("Loading programme %s..." % index)
			self._ui_episode.set_text("")
			self._ui_duration.set_text("")
			self._ui_expiry.set_text("")
			self._ui_desc.get_buffer().set_text("")
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
			try:
				duration = int(duration) # seconds
				duration = str(duration // 60) + " minutes"
			except ValueError:
				# Wasn't a number, try mins:seconds format
				try:
					minutes, seconds = duration.split(":")
					minutes = int(minutes)
					seconds = int(seconds)
					if seconds >= 30:
						minutes += 1
					duration = str(minutes) + " minutes"
				except ValueError:
					pass # Leave as it is
			self._ui_duration.set_text(duration)
			timetoexpiry = info.get("expiryrel")
			if timetoexpiry or "versions" in info:
				self._ui_programme_info.set_sensitive(True)
			self._ui_expiry.set_markup(
				"Expires %s" % timetoexpiry
				if timetoexpiry
				else (''
					if "versions" in info or not info.get("hasexpired", False)
					else "<span foreground='red'><b>Expired</b></span>"))
			self._ui_desc.get_buffer().set_text(info.get("desc", "No description"))
			self._ui_mode_list.get_model().clear()
			self._ui_version_list.get_model().clear()
			active_version_iter = None
			for version in info.get("versions", "").split(","):
				if version:
					version_iter = self._ui_version_list.get_model().append([version])
					if version == self.config.config_preferred_version:
						active_version_iter = version_iter
			self._mode_callback_id = self._ui_version_list.connect("changed", self._version_selected_cb, index, info)
			if active_version_iter is not None:
				self._ui_version_list.set_active_iter(active_version_iter)
			else:
				self._ui_version_list.set_active(0)

			# Need to load image on another thread
			thumb = info.get("thumbnail")
			if thumb:
				load_image_in_background(self._ui_thumb, thumb,
					cancelcheck=lambda: self.showing_info != index,
					transform=lambda pb: ensure_image_small(pb, 150, 100))

		def on_fail(errs):
			self._ui_programme_info.hide_all()
			self.show_errors("loading programme information")(errs)

		def finished(result, errs):
			if len(errs) == 1 and errs[0].startswith("WARNING: No programmes are available for this pid"):
				errs = [] # Programme has expired
				result["hasexpired"] = True
			if errs:
				gobject.idle_add(on_fail, errs)
			else:
				gobject.idle_add(got_info, result)

		self.gip.get_programme_info(index).on_complete(always=finished)

	def show_errors(self, activity=None):
		'''Creates a function that can display a list of errors.'''
		message = "There were %s errors%s:\n%s"
		activitystr = " while %s" % (activity,) if activity is not None else ""
		def show_errs(errs):
			dlg = gtk.MessageDialog(
				parent=self.totem.get_main_window(),
				flags=gtk.DIALOG_MODAL|gtk.DIALOG_DESTROY_WITH_PARENT,
				type=gtk.MESSAGE_ERROR,
				buttons=gtk.BUTTONS_OK,
				message_format=message % (len(errs), activitystr, "\n".join(errs))
			)
			dlg.run()
			dlg.destroy()
		return lambda errs: gobject.idle_add(show_errs, errs)

	def show_errors_and_cancel_populate(self, populate, activity=None):
		title = "Failed to load"
		if activity is not None:
			title += " %ss" % (activity,)
		def pop_and_show(errs):
			gobject.idle_add(populate, [(TreeValues(title, loaded=True), [])])
			self.show_errors("populating %ss" % (activity,))(errs)
		return pop_and_show


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
		if pb is None or cancelcheck is None or cancelcheck():
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
		if pb is not None and transform is not None:
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
		self._uiconfig_flvstreamer_location.add_filter(self.__get_filter("Streamer", "rtmpdump", "flvstreamer"))
		self._uiconfig_flvstreamer_guess = builder.get_object("config_flvstreamer_locdefault")
		self._uiconfig_flvstreamer_guess.connect("toggled",
			lambda button: self._intelligent_guess_clicked_cb(self._uiconfig_flvstreamer_location, button))

		self._uiconfig_ffmpeg_location = builder.get_object("config_ffmpeg_loc")
		self._uiconfig_ffmpeg_location.add_filter(self.__get_filter("ffmpeg", "ffmpeg"))
		self._uiconfig_ffmpeg_guess = builder.get_object("config_ffmpeg_locdefault")
		self._uiconfig_ffmpeg_guess.connect("toggled",
			lambda button: self._intelligent_guess_clicked_cb(self._uiconfig_ffmpeg_location, button))

		self._uiconfig_search_type = builder.get_object("config_search_type")

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

		self._uiconfig_preferred_version = builder.get_object("config_preferred_version")
		self._uiconfig_preferred_bitrate = builder.get_object("config_preferred_bitrate")
		self._uiconfig_preferred_bitrate.add_mark(300, gtk.POS_BOTTOM, "low")
		self._uiconfig_preferred_bitrate.add_mark(500, gtk.POS_BOTTOM, "std")
		self._uiconfig_preferred_bitrate.add_mark(1200, gtk.POS_BOTTOM, "high")
		self._uiconfig_preferred_bitrate.add_mark(2500, gtk.POS_BOTTOM, "hd")

		self._uiconfig_localfiles_directories = builder.get_object("config_local_files_directories")
		localfiles_entry = builder.get_object("config_local_files_newdirectory")
		builder.get_object("config_local_files_add").connect("clicked", self._localfiles_add_cb, localfiles_entry)
		localfiles_entry.connect("activate", self._localfiles_add_cb, localfiles_entry)
		builder.get_object("config_local_files_remove").connect("clicked", self._localfiles_remove_cb, localfiles_entry)

	def create_configure_dialog(self, *args):
		self._init_ui(self.config_getiplayer_location, self._uiconfig_getiplayer_location, self._uiconfig_getiplayer_guess)
		self._init_ui(self.config_flvstreamer_location, self._uiconfig_flvstreamer_location, self._uiconfig_flvstreamer_guess)
		self._init_ui(self.config_ffmpeg_location, self._uiconfig_ffmpeg_location, self._uiconfig_ffmpeg_guess)

		selected_search_type = None
		search_type_model = self._uiconfig_search_type.get_model()
		for row in search_type_model:
			if search_type_model.get_value(row.iter, 1) == self.config_search_type:
				selected_search_type = row.iter
		self._uiconfig_search_type.set_active_iter(selected_search_type)

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

		self._uiconfig_preferred_version.set_active(0)
		for row in self._uiconfig_preferred_version.get_model():
			if row[0] == self.config_preferred_version:
				self._uiconfig_preferred_version.set_active_iter(row.iter)
				break

		self._uiconfig_preferred_bitrate.set_value(self.config_preferred_bitrate)

		localfiles_model = self._uiconfig_localfiles_directories.get_model()
		localfiles_model.clear()
		for directory in self.config_localfiles_directories:
			localfiles_model.append([directory])

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

	def __get_filter(self, name, *exenames):
		filter = gtk.FileFilter()
		filter.set_name(name + " executable")
		for exename in exenames:
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

	def _localfiles_add_cb(self, button, localfiles_entry):
		directory = localfiles_entry.get_text()
		directory = os.path.expanduser(directory)
		if not os.path.isabs(directory):
			dlg = gtk.MessageDialog(
				parent=self.config_dialog,
				flags=gtk.DIALOG_MODAL|gtk.DIALOG_DESTROY_WITH_PARENT,
				buttons=gtk.BUTTONS_OK,
				message_format="Only absolute paths can be used here. Invalid: %s" % (directory,)
			)
			dlg.run()
			dlg.destroy()
			return
		localfiles_store = self._uiconfig_localfiles_directories.get_model()
		if not directory:
			return
		if directory in [row[0] for row in localfiles_store]:
			return
		localfiles_store.append([directory])
		localfiles_entry.set_text("")

	def _localfiles_remove_cb(self, button, localfiles_entry):
		localfiles_store, active_iter = self._uiconfig_localfiles_directories.get_selection().get_selected()
		if active_iter:
			localfiles_store.remove(active_iter)

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

		search_type_iter = self._uiconfig_search_type.get_active_iter()
		self.config_search_type = self._uiconfig_search_type.get_model().get_value(search_type_iter, 1)

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

		version_iter = self._uiconfig_preferred_version.get_active_iter()
		self.config_preferred_version = self._uiconfig_preferred_version.get_model().get_value(version_iter, 0)

		self.config_preferred_bitrate = self._uiconfig_preferred_bitrate.get_value()

		self.config_localfiles_directories = [row[0] for row in self._uiconfig_localfiles_directories.get_model()]

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

	@property
	def config_search_type(self):
		st = self.gconf.get_string(GCONF_KEY + "/search_type")
		return st if st in ["word", "wildcard", "regex"] else "wildcard"

	@config_search_type.setter
	def config_search_type(self, value):
		self.gconf.set_string(GCONF_KEY + "/search_type", value)

	@property
	def config_preferred_version(self):
		return self.gconf.get_string(GCONF_KEY + "/preferred_version") or "default"

	@config_preferred_version.setter
	def config_preferred_version(self, value):
		self.gconf.set_string(GCONF_KEY + "/preferred_version", value)

	@property
	def config_preferred_bitrate(self):
		pb = self.gconf.get_int(GCONF_KEY + "/preferred_bitrate")
		return pb if pb > 0 else 300

	@config_preferred_bitrate.setter
	def config_preferred_bitrate(self, value):
		self.gconf.set_int(GCONF_KEY + "/preferred_bitrate", int(value))

	@property
	def config_localfiles_directories(self):
		return self.gconf.get_list(GCONF_KEY + "/localfiles_directories", gconf.VALUE_STRING)

	@config_localfiles_directories.setter
	def config_localfiles_directories(self, value):
		self.gconf.set_list(GCONF_KEY + "/localfiles_directories", gconf.VALUE_STRING, value)

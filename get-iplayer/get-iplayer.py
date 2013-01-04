# -*- coding: utf-8 -*-

import totem
import gobject
from getiplayer_interface import GetIPlayer

class GetIplayerPlugin (totem.Plugin):
	def __init__ (self):
		totem.Plugin.__init__ (self)
		self.totem = None

	def activate (self, totem_object):
		# Build the interface
		builder = self.load_interface ("get-iplayer.ui", True, totem_object.get_main_window (), self)
		container = builder.get_object ('getiplayer_pane')

		self.gip = GetIPlayer("/home/andyrooger/git/get_iplayer/get_iplayer")
		progs_store = builder.get_object("getiplayer_progs_store")
		progs_list = builder.get_object("getiplayer_progs_list")
		progs_list.connect("row-expanded", self._row_expanded_cb)

		self.totem = totem_object
		container.show_all ()

		# Add the interface to Totem's sidebar
		self.totem.add_sidebar_page ("get-iplayer", _("Get iPlayer"), container)

		self._populate_types(progs_list)

	def deactivate (self, totem_object):
		totem_object.remove_sidebar_page ("get-iplayer")

	def _row_expanded_cb(self, tree, iter, path):
		depth = tree.get_model().iter_depth(iter)
		if depth == 0:
			self._populate_channels(tree, iter)
		elif depth == 1:
			self._populate_categories(tree, iter)

	def _populate_types(self, progs_list):
		populate = load_branch(progs_list, None)
		def got_types(types):
			populate([([t, False, False], True) for t in types])
		self.gip.get_types().on_complete(got_types)

	def _populate_channels(self, progs_list, branch):
		type_name = progs_list.get_model().get_value(branch, 0)
		populate = load_branch(progs_list, branch)
		if populate is None:
			return # Already loading
		def got_channels(channels):
			populate([([c, False, False], True) for c in channels])
		self.gip.get_channels(type=type_name).on_complete(got_channels)

	def _populate_categories(self, progs_list, branch):
		progs_store = progs_list.get_model()
		channel_name = progs_store.get_value(branch, 0)
		type_name = progs_store.get_value(progs_store.iter_parent(branch), 0)

		populate = load_branch(progs_list, branch)
		if populate is None:
			return # Already loading
		def got_categories(cats):
			populate([([c, False, False], False) for c in cats])
		self.gip.get_categories(type=type_name, channel=channel_name).on_complete(got_categories)

def is_branch_loaded(treestore, branch_iter):
	if branch_iter is None:
		return treestore.iter_n_children(branch_iter) > 0
	else:
		return treestore.get_value(branch_iter, 2)
	

def is_branch_loading(treestore, branch_iter):
	if treestore.iter_n_children(branch_iter) != 1:
		return False
	return treestore.get_value(treestore.iter_children(branch_iter), 1)

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
		treestore.append(branch_iter, ["Loading...", True, False])
		if expansion_state is not None:
			if expansion_state: tree.expand_row(branch_path, False)
			else: tree.collapse_row(branch_path)
	gobject.idle_add(start_load)

	def populate(children):
		branch_iter = None if branch_path is None else treestore.get_iter(branch_path)
		expansion_state = None if branch_path is None else tree.row_expanded(branch_path)

		if not is_branch_loading(treestore, branch_iter):
			# Should only populate if a load is supposed to be in progress currently
			# Force means we can populate when a load is complete
			allowed_by_force = force and is_branch_loaded(treestore, branch_iter)
			if not allowed_by_force:
				return
		if branch_iter is not None:
			treestore.set_value(branch_iter, 2, True)

		# Remove ALL children (in case we used force)
		child = treestore.iter_children(branch_iter)
		while child:
			treestore.remove(child)
			child = treestore.iter_children(branch_iter)
		for c, expandable in children:
			c_iter = treestore.append(branch_iter, c)
			if expandable:
				treestore.append(c_iter, ["Nothing", False, False])

		if expansion_state is not None:
			if expansion_state: tree.expand_row(branch_path, False)
			else: tree.collapse_row(branch_path)
		return False
	return lambda children: gobject.idle_add(populate, children)


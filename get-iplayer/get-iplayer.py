# -*- coding: utf-8 -*-

import totem
import gobject
from getiplayer_interface import GetIPlayer

class GetIplayerPlugin (totem.Plugin):
	def __init__ (self):
		totem.Plugin.__init__ (self)
		self.totem = None
		self.filter_order = ["type", "channel", "category"]

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

	def _filter_at_branch(self, progs_store, branch):
		node_names = []
		while branch is not None:
			node_names.append(progs_store.get_value(branch, 0))
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
			populate([([f, False, False], []) for f in filters])
		populating = self.filter_order[populate_depth]
		active_filters = self._active_filters(progs_store, branch)
		self.gip.get_filters_and_blanks(populating, **active_filters).on_complete(got_filters)

	def _populate_series_and_episodes(self, progs_list, branch):
		populate = load_branch(progs_list, branch)
		if populate is None:
			return
		def got_episodes(series):
			populate([
				([s, False, False], [([ep[1], False, False], None) for ep in eps])
				for s, eps in series.iteritems()
			])
		active_filters = self._active_filters(progs_list.get_model(), branch)
		self.gip.get_episodes(**active_filters).on_complete(got_episodes)

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
			treestore.set_value(branch_iter, 2, True)

		# Remove ALL children (in case we used force)
		child = treestore.iter_children(branch_iter)
		while child:
			treestore.remove(child)
			child = treestore.iter_children(branch_iter)
		def add_children(child_list, branch):
			for c, subc in child_list:
				c_iter = treestore.append(branch, c)
				if subc == []:
					treestore.append(c_iter, ["Nothing", False, False])
				elif subc is not None:
					add_children(subc, c_iter)
		add_children(children, branch_iter)

		if expansion_state is not None:
			if expansion_state: tree.expand_row(branch_path, False)
			else: tree.collapse_row(branch_path)
		return False
	return lambda children: gobject.idle_add(populate, children)


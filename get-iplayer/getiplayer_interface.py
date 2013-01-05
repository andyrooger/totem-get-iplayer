'''
Interfaces with the getiplayer program to query program listings and download etc.
'''

import sys
import re
import threading
import subprocess

RE_LISTING_ENTRY = re.compile(r"^(.+) \((\d+?)\)$", re.MULTILINE)
RE_MATCH_TOTAL = re.compile(r"^INFO: (\d+) Matching Programmes$", re.MULTILINE)
RE_TREE_EPISODE = re.compile(r"^  (\d+?): \((\d*?)\) (.*)$")
RE_INFO_LINE = re.compile(r"^(.+?):\s+(.+)$", re.MULTILINE)

def parse_listings(input, withcounts=False):
	listings = RE_LISTING_ENTRY.finditer(input)
	for match in listings:
		name, count = match.groups()
		name = name.strip()
		count = int(count)
		if withcounts:
			yield (name, count)
		else:
			yield name

def parse_match_count(input):
	count = RE_MATCH_TOTAL.search(input)
	if count is None:
		raise ValueError("Unexpected format from get_iplayer output")
	return int(count.groups()[0])

def parse_episodes(input):
	episodes = {} # Series name : list of episode index, number and names
	series = None
	for line in input.splitlines():
		match = RE_TREE_EPISODE.match(line)
		if not match:
			series = line
			continue
		series_list = episodes.get(series, [])
		episodes[series] = series_list # In case we got list through default
		idx, num, name = match.groups()
		series_list.append((int(idx), int(num) if num else 0, name))
	# Need to sort by episode number and then drop number from data
	sorted_eps = {}
	for series_name, series_episodes in episodes.iteritems():
		sorted_eps[series_name] = [(i, name) for i, n, name in sorted(series_episodes, key=lambda ep: ep[1])]
	return sorted_eps

def parse_info(input):
	relevant = input.split("\n\n")[-2]
	discovered_info = RE_INFO_LINE.finditer(relevant)
	info = {}
	for i in discovered_info:
		name, value = i.groups()
		info[name] = value
	return info

def parse_versions(version_collections):
	versions = set()
	for vc in version_collections:
		versions.update(vc.split(","))
	versions.discard("")
	return list(versions)

class PendingResult(object):
	def __init__(self, hasresult, getresult):
		self._resultlock = threading.Lock()
		self._waiterlock = threading.Lock()
		self._hasresult = hasresult
		self._getresult = getresult
		self._result = None
		self._gotresult = False
		self._callbacks = []

	def has_result(self):
		return self._hasresult()

	def get_result(self):
		with self._resultlock:
			if self._gotresult:
				return self._result
			self._result = self._getresult()
			self._hasresult = lambda: True
			self._gotresult = True
		return self._result

	def on_complete(self, callback):
		with self._waiterlock:
			thread_exists = bool(self._callbacks)
			self._callbacks.append(callback)
			if not thread_exists:
				def run(self):
					res = self.get_result()
					with self._waiterlock:
						for cb in self._callbacks:
							cb(res)
						self._callbacks = []
				threading.Thread(target=run, args=(self,)).start()

	def translate(self, trans):
		return PendingResult(self._hasresult, lambda: trans(self.get_result()))

class GetIPlayer(object):
	def __init__(self, location):
		self.location = location
		self.recordings = []

	def _call(self, *vargs, **kwargs):
		args = [self.location]
		args.extend(str(v) for v in vargs)
		for k, v in kwargs.iteritems():
			arg = "-" + k if len(k) is 1 else "--" + k
			if v:
				arg += (" " if len(k) is 1 else "=") + v
			args.append(arg)
		proc = subprocess.Popen(args, stdout=subprocess.PIPE)
		def get_result():
			stdout, stderr = proc.communicate()
			#print stderr - should already be happening
			return stdout
			
		return PendingResult(lambda: proc.poll() is not None, get_result)

	def _fix_blank_search(self, **kwargs):
		if "channel" in kwargs and not kwargs["channel"]:
			kwargs["exclude-channel"] = ".+"
			kwargs["channel"] = ".*"
		if "category" in kwargs and not kwargs["category"]:
			kwargs["exclude-category"] = ".+"
			kwargs["category"] = ".*"
		return kwargs


	def get_filters_and_blanks(self, filter_type, type="all", channel=".*", category=".*", version=".*"):
		normal_filters = self.get_filters(filter_type, type, channel, category, version)
		missing_filters = self.count_missing_attrib(filter_type, type, channel, category, version)
		def complete_filters():
			filters = normal_filters.get_result()
			if missing_filters.get_result() > 0:
				filters.insert(0, "")
			return filters
		return PendingResult(lambda: normal_filters.has_result() and missing_filters.has_result(), complete_filters)

	def get_filters(self, filter_type, type="all", channel=".*", category=".*", version=".*"):
		if filter_type == "category":
			filter_type = "categories"
		if filter_type == "version":
			filter_type = "versions"
		fixed_filtering = self._fix_blank_search(type=type, channel=channel, category=category, version=version)
		filters = self._call(list=filter_type, **fixed_filtering)
		available_filters = filters.translate(lambda fs: list(parse_listings(fs)))
		if filter_type == "versions":
			return available_filters.translate(parse_versions)
		else:
			return available_filters

	def count_missing_attrib(self, blankattrib, type="all", channel=".*", category=".*", version=".*"):
		'''Counts the number of programmes with the given attribute blank, but that fit the other filters.'''
		if blankattrib == "type" or blankattrib == "version":
			return PendingResult(lambda: True, lambda: 0) # Don't have an option to exclude these, but I don't think you can have blank types
		exclude = {}
		exclude["exclude-"+blankattrib] = ".+"
		blank = self._call(type=type, channel=channel, category=category, **exclude)
		return blank.translate(parse_match_count)

	def get_episodes(self, type="all", channel=".*", category=".*", version=".*"):
		fixed_filtering = self._fix_blank_search(type=type, channel=channel, category=category, version=version)
		episodes = self._call(tree="", listformat="<index>: (<episodenum>) <episode>", **fixed_filtering)
		return episodes.translate(parse_episodes)

	def get_programme_info(self, index):
		info = self._call(index, info="")
		return info.translate(parse_info)

	def record_programme(self, index):
		self.recordings.append(index)
		recording = self._call(index, get="", q="")
		recording.on_complete(lambda _: self.recordings.remove(index) if index in self.recordings else None)
		return recording

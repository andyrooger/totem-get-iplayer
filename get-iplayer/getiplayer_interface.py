'''
Interfaces with the getiplayer program to query program listings and download etc.
'''

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

import sys
import re
import threading
import subprocess
import os.path

RE_LISTING_ENTRY = re.compile(r"^(.+) \((\d+?)\)$", re.MULTILINE)
RE_MATCH_TOTAL = re.compile(r"^INFO: (\d+) Matching Programmes$", re.MULTILINE)
RE_TREE_EPISODE = re.compile(r"^  (\d+?): \((\d*?)\) (.*)$")
RE_INFO_LINE = re.compile(r"^(.+?):\s+(.+)$", re.MULTILINE)
RE_HISTORY = re.compile(r"^\((\d+)\):\((.+)\):\((.+)\):\((.+)\):\((.+)\):\((.+)\)$", re.MULTILINE)

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

def parse_info(input, versions):
	relevant = input.split("\n\n")[-2]
	discovered_info = RE_INFO_LINE.finditer(relevant)
	info = {}
	for i in discovered_info:
		name, value = i.groups()
		version = ""
		for v in versions:
			if value.startswith(v+":"):
				version = v
				value = value[len(v)+1:].lstrip()
		current_versions = info.get(name, {})
		current_versions[version] = value
		info[name] = current_versions
	# Remove version dicts for things with no versions
	clean_info = {}
	for k, v in info.iteritems():
		if list(v.keys()) == [""]:
			clean_info[k] = v[""]
		else:
			clean_info[k] = v
	return clean_info

def parse_versions(version_collections):
	versions = set()
	for vc in version_collections:
		versions.update(vc.split(","))
	versions.discard("")
	return list(versions)

def parse_history(input, guess_versions):
	for match in RE_HISTORY.finditer(input):
		index, name, episode, version, mode, location = match.groups()
		index = int(index)
		if guess_versions:
			filename = os.path.basename(location)
			filename = os.path.splitext(filename)[0]
			version = filename.rsplit("_", 1)[-1]
		yield (index, name, episode, version, mode, location)

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

	def then(self, tonext):
		translated = self.translate(tonext)
		def hasresult():
			return translated.has_result() and translated.get_result().has_result()
		def getresult():
			return translated.get_result().get_result()
		return PendingResult(hasresult, getresult)

class GetIPlayer(object):
	def __init__(self, location, flvstreamerloc=None, ffmpegloc=None, output_location="~/.totem-get-iplayer"):
		self.stock_vargs = [location]
		self.stock_kwargs = {}
		if flvstreamerloc is not None:
			self.stock_kwargs["flvstreamer"] = flvstreamerloc
		if ffmpegloc is not None:
			self.stock_kwargs["ffmpeg"] = ffmpegloc
		self.recordings = {}
		self._version_result = self.get_filters("version")
		self.output_location = os.path.abspath(os.path.expanduser(output_location))

	def _parse_args(self, vargs, kwargs):
		args = list(self.stock_vargs)
		args.extend(str(v) for v in vargs)
		for k, v in dict(self.stock_kwargs, **kwargs).iteritems():
			arg = "-" + k if len(k) is 1 else "--" + k
			if v:
				arg += (" " if len(k) is 1 else "=") + v
			args.append(arg)
		return args

	def __call(self, stdout, *vargs, **kwargs):
		'''Call and return the new process.'''
		args = self._parse_args(vargs, kwargs)
		return subprocess.Popen(args, stdout=stdout)

	def _call_stream(self, stdout, *vargs, **kwargs):
		'''Call and return whatever stdout was (expects an fd or pipe).'''
		self.__call(stdout, *vargs, **kwargs)
		return stdout

	def _call(self, *vargs, **kwargs):
		'''Calls and returns pending result for output.'''
		proc = self.__call(subprocess.PIPE, *vargs, **kwargs)
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


	def get_filters_and_blanks(self, filter_type, search=None, type="all", channel=".*", category=".*", version=".*"):
		normal_filters = self.get_filters(filter_type, search, type, channel, category, version)
		missing_filters = self.count_missing_attrib(filter_type, search, type, channel, category, version)
		def complete_filters():
			filters = normal_filters.get_result()
			if missing_filters.get_result() > 0:
				filters.insert(0, "")
			return filters
		return PendingResult(lambda: normal_filters.has_result() and missing_filters.has_result(), complete_filters)

	def get_filters(self, filter_type, search=None, type="all", channel=".*", category=".*", version=".*"):
		if filter_type == "category":
			filter_type = "categories"
		if filter_type == "version":
			filter_type = "versions"
		fixed_filtering = self._fix_blank_search(type=type, channel=channel, category=category, version=version)
		filters = self._call(*([search] if search else []), list=filter_type, **fixed_filtering)
		available_filters = filters.translate(lambda fs: list(parse_listings(fs)))
		if filter_type == "versions":
			return available_filters.translate(parse_versions)
		else:
			return available_filters

	def count_missing_attrib(self, blankattrib, search=None, type="all", channel=".*", category=".*", version=".*"):
		'''Counts the number of programmes with the given attribute blank, but that fit the other filters.'''
		if blankattrib == "type" or blankattrib == "version":
			return PendingResult(lambda: True, lambda: 0) # Don't have an option to exclude these, but I don't think you can have blank types
		exclude = {}
		exclude["exclude-"+blankattrib] = ".+"
		blank = self._call(*([search] if search else []), type=type, channel=channel, category=category, **exclude)
		return blank.translate(parse_match_count)

	def get_episodes(self, search=None, type="all", channel=".*", category=".*", version=".*"):
		fixed_filtering = self._fix_blank_search(type=type, channel=channel, category=category, version=version)
		episodes = self._call(*([search] if search else []), tree="", listformat="<index>: (<episodenum>) <episode>", **fixed_filtering)
		return episodes.translate(parse_episodes)

	def get_programme_info(self, index, availableversions=None):
		'''
		Need one or more of availableversions to be available for this programme or we get incomplete info.
		If they are not provided then we fetch them first.
		'''
		if availableversions is None:
			return self._version_result.then(lambda vs: self.get_programme_info(index, vs))
		info = self._call(index, info="", versions=",".join(availableversions))
		return info.translate(lambda i: parse_info(i, availableversions))

	def record_programme(self, index, displayname=None, version="default", mode="best"):
		if displayname is None:
			displayname = "Programme %s" % index
		self.recordings[index] = (displayname, version, mode)
		recording = self._call(index, output=self.output_location, get="", q="", versions=version, modes=mode)
		recording.on_complete(lambda _: self.recordings.pop(index, None))
		return recording

	def get_history(self, guess_version=True):
		history = self._call(history="", listformat="(<index>):(<name>):(<episode>):(<versions>):(<mode>):(<filename>)")
		return history.translate(lambda h: list(parse_history(h, guess_version)))

	def stream_programme_to_external(self, index, version="default", mode="best", stream_cmd="totem fd://0 --no-existing-session"):
		'''Stream a program to an external program's stdin.'''
		return self._call(index, versions=version, modes=mode, stream="", player=stream_cmd, q="")

	def stream_programme_to_pipe(self, index, version="default", mode="best"):
		'''Stream a program to the current stdin.'''
		rfd, wfd = os.pipe()
		self._call_stream(wfd, index, versions=version, modes=mode, stream="", q="")
		return rfd

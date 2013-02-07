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
import signal
import os.path
from collections import OrderedDict, defaultdict

RE_LISTING_ENTRY = re.compile(r"^(.+) \((\d+?)\)$", re.MULTILINE)
RE_MATCH_TOTAL = re.compile(r"^INFO: (\d+) Matching Programmes$", re.MULTILINE)
RE_TREE_EPISODE = re.compile(r"^  (\d+?): \((\d*?)\) (.*)$")
RE_INFO_LINE = re.compile(r"^(.+?):\s+(.+)$", re.MULTILINE)
RE_HISTORY = re.compile(r"^\((\d+)\):\((.+)\):\((.+)\):\((.+)\):\((.+)\):\((.+)\)$", re.MULTILINE)
RE_MODE_GROUP = re.compile(r"^([^\d]*?)\d*$")
RE_STREAMINFO_LINE = re.compile(r"^([a-zA-Z]+):\s+(.*)$")
RE_SUBTITLE_LOCATION = re.compile(r"^INFO: Downloading Subtitles to '(.*)'$", re.MULTILINE)

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

def parse_streaminfo(input, includesubtitles=False):
	streaminfo = defaultdict(dict)
	streamtitle = None
	for line in input.splitlines():
		parsed = RE_STREAMINFO_LINE.search(line)
		if parsed:
			key, value = parsed.groups()
			value = value.strip() # key will already be stripped, but value may have newline
			if key == "stream":
				streamtitle = value
			elif streamtitle is not None:
				streaminfo[streamtitle][key] = value
		else:
			streamtitle = None # Blank line, separates sections
	if not includesubtitles:
		for stream in streaminfo.keys():
			if stream.startswith("subtitle"):
				del streaminfo[stream]
	return streaminfo

def parse_versions(version_collections):
	versions = set()
	for vc in version_collections:
		versions.update(vc.split(","))
	versions.discard("")
	return list(versions)

def combine_modes(info):
	'''
	Combine modes of the same type e.g. flashlow1 and flashlow2 get grouped to flashlow.
	Only do this if all members of the group have equivalent additional info.
	'''
	bygroup = defaultdict(set)
	for mode, addinfo in info:
		group = RE_MODE_GROUP.search(mode)
		group = group.group(1) if group else None # e.g. take flashlow from flashlow2
		bygroup[group].add((mode, addinfo))
	groupedwherepossible = {}
	for group, members in bygroup.iteritems():
		if len(set(addinfo for mode, addinfo in members)) == 1: # All sizes equal
			groupedwherepossible[group] = members.pop()[1]
		else:
			groupedwherepossible.update(members)
	return groupedwherepossible

def parse_modes(info, version, includesubtitle=False):
	'''Parse modes from the object returned from get_programme_info()'''
	# Combine modes with size and without to create a dictionary of all modes (with sizes where we have them)
	modes_nosize = info.get("modes", {}).get(version, "")
	modes_nosize = {mode: None for mode in modes_nosize.split(",")} if modes_nosize else {}
	modes_size = info.get("modesizes", {}).get(version, "")
	modes_size = dict(mode.split("=") for mode in modes_size.split(",")) if modes_size else {}
	combined = dict(modes_nosize, **modes_size)

	# Combine modes of the same type e.g. flashlow1 and flashlow2 get grouped to flashlow
	# Only do this if all members of the group are of equal size
	groupedwherepossible = combine_modes(combined.iteritems())

	def size_from_string(asstr):
		if asstr is None:
			return 0
		elif asstr.endswith("MB"):
			return int(asstr[:-2])
		else:
			sys.stderr.write("Unrecognised mode size string: %s" % (asstr,))
			return 0

	# And order by the int version of the size string, adding "best" at the top
	ordered = OrderedDict(best=None)
	ordered.update(sorted(groupedwherepossible.iteritems(), key=lambda it: -size_from_string(it[1])))
	for stream in ordered.keys():
		if stream.startswith("subtitle"):
			del ordered[stream]
	return ordered

def parse_history(input, guess_versions):
	for match in RE_HISTORY.finditer(input):
		index, name, episode, version, mode, location = match.groups()
		index = int(index)
		if guess_versions:
			filename = os.path.basename(location)
			filename = os.path.splitext(filename)[0]
			version = filename.rsplit("_", 1)[-1]
		yield (index, name, episode, version, mode, location)

def parse_subtitles(input):
	'''Try to parse the results from a subtitle retrieve, returning the subtitle file location or None if there is none.'''
	match = RE_SUBTITLE_LOCATION.search(input)
	if match:
		return match.group(1)
	else:
		return None

def is_error_line(line):
	'''Is this line a real error line?'''
	if line.startswith("ERROR:") or line.startswith("WARNING:"):
		return "localfile" not in line # Ignore errors about localfiles plugin
	return False

class PendingResult(object):
	def __init__(self, hasresult, getresult, showserrors):
		self._resultlock = threading.Lock()
		self._waiterlock = threading.Lock()
		self._hasresult = hasresult
		self._getresult = getresult
		self._showserrors = showserrors # value of getresult() will be treated as (result, [errors])
		self._result = None
		self._errors = []
		self._gotresult = False
		self._callbacks = []

	def has_result(self):
		return self._hasresult()

	def get_result(self):
		with self._resultlock:
			if self._gotresult:
				return self._result
			r = self._getresult()
			if self._showserrors:
				self._result, self._errors = r
			else:
				self._result = r
			self._hasresult = lambda: True
			self._gotresult = True
		return self._result

	def get_errors(self):
		self.get_result()
		return self._errors

	def on_complete(self, callback=None, onerror=None, always=None):
		'''
		Run a function when the result is complete. At most one of callback or onerror is ever run.
		If neither are specified then neither will be run, otherwise onerror is run when it is specified and there
		is an error, or callback is run the rest of the time.
		Always is always run (if given) and given both normal and error outputs.
		'''
		with self._waiterlock:
			thread_exists = bool(self._callbacks)
			self._callbacks.append((callback, onerror))
			if not thread_exists:
				def run(self):
					res = self.get_result()
					err = self.get_errors()
					with self._waiterlock:
						for cb, oe in self._callbacks:
							if oe is not None and err:
								oe(err)
							elif cb is not None:
								cb(res)
							if always is not None:
								always(res, err)
						self._callbacks = []
				threading.Thread(target=run, args=(self,)).start()

	def translate(self, trans):
		if self._showserrors:
			return PendingResult(self._hasresult, lambda: (trans(self.get_result()), self.get_errors()), True)
		else:
			return PendingResult(self._hasresult, lambda: trans(self.get_result()), False)

	def then(self, tonext):
		translated = self.translate(tonext)
		def hasresult():
			return translated.has_result() and translated.get_result().has_result()
		def getresult():
			r = translated.get_result()
			if self._showserrors:
				return (r.get_result(), translated.get_errors() + r.get_errors())
			else:
				return r.get_result()
		return PendingResult(hasresult, getresult, self._showserrors)

	def redistribute_streams(self, iserrorstd=None, iserrorerr=None):
		'''
		get_iplayer doesn't necessarily use error stream for errors and stdout for normal output.
		This lets us redistribute the streams.
		iserror methods for each stream return whether a given line is an error line.
		This only works when the normal result is text!
		'''
		def getresult():
			stdout = []
			stderr = []
			if iserrorstd is None:
				stdout += self.get_result().splitlines()
			else:
				for line in self.get_result().splitlines():
					(stderr if iserrorstd(line) else stdout).append(line)

			if not self._showserrors:
				return "\n".join(stdout)

			if iserrorerr is None:
				stderr += self.get_errors()
			else:
				for line in self.get_errors():
					(stderr if iserrorerr(line) else stdout).append(line)

			stdout = "\n".join(stdout)
			return (stdout, stderr)
			
		return PendingResult(self.has_result, getresult, self._showserrors)

	@classmethod
	def constant(cls, c):
		return PendingResult(lambda: True, lambda: (c, []), True)

	@classmethod
	def all(cls, **pendingresults):
		showerrors = any(p._showserrors for p in pendingresults.itervalues())
		def hasresult():
			return all(p.has_result() for p in pendingresults.itervalues())
		def getresult():
			res = {k: p.get_result() for (k, p) in pendingresults.iteritems()}
			if showerrors:
				return (res, [err for p in pendingresults.itervalues() for err in p.get_errors()])
			else:
				return res
		return PendingResult(hasresult, getresult, showerrors)

class GetIPlayer(object):
	def __init__(self, location, flvstreamerloc=None, ffmpegloc=None, output_location="~/.totem-get-iplayer"):
		self.stock_vargs = [location]
		self.stock_kwargs = {}
		if flvstreamerloc is not None:
			self.stock_kwargs["flvstreamer"] = flvstreamerloc
		if ffmpegloc is not None:
			self.stock_kwargs["ffmpeg"] = ffmpegloc
		self.recordings = {}
		self._running_processes = {}
		self.output_location = os.path.abspath(os.path.expanduser(output_location))
		self._version_result = self.get_filters("version")

	def close(self):
		for proc in self._running_processes.itervalues():
			if proc.poll() is None:
				try:
					os.killpg(proc.pid, signal.SIGKILL)
				except OSError:
					sys.stderr.write("Could not terminate process %s" % (proc.pid,))

	def close_main_stream(self):
		MAIN_STREAM_LIMITER.close()

	def _parse_args(self, vargs, kwargs):
		args = list(self.stock_vargs)
		args.extend(str(v) for v in vargs)
		for k, v in dict(self.stock_kwargs, **kwargs).iteritems():
			arg = "-" + k if len(k) is 1 else "--" + k
			if v:
				arg += (" " if len(k) is 1 else "=") + v
			args.append(arg)
		return args

	def __add_running_process(self, proc):
		self._running_processes[proc.pid] = proc
		def remove_from_running():
			if proc.pid in self._running_processes:
				del self._running_processes[proc.pid]
		return remove_from_running
		

	def __call(self, stdout, stderr, *vargs, **kwargs):
		'''Call and return the new process.'''
		args = self._parse_args(vargs, kwargs)
		return subprocess.Popen(args, preexec_fn=os.setsid, stdout=stdout, stderr=stderr)

	def _call_stream(self, stdout, *vargs, **kwargs):
		'''Call and return whatever stdout was (expects an fd or pipe).'''
		proc = self.__call(stdout, None, *vargs, **kwargs)
		MAIN_STREAM_LIMITER.add_process(proc)
		procdone = self.__add_running_process(proc)
		PendingResult(lambda: proc.poll() is not None, proc.wait, False).on_complete(lambda _: procdone())
		return stdout

	def _call(self, *vargs, **kwargs):
		'''Calls and returns pending result for output.'''
		proc = self.__call(subprocess.PIPE, subprocess.PIPE, *vargs, **kwargs)
		def get_result():
			stdout, stderr = proc.communicate()
			return (stdout, stderr.splitlines())
		procdone = self.__add_running_process(proc)
		result = PendingResult(lambda: proc.poll() is not None, get_result, True)
		result.on_complete(lambda _: procdone())
		return result.redistribute_streams(is_error_line, is_error_line)

	def _call_no_refresh(self, *vargs, **kwargs):
		'''Like _call but ensures no unexpected refresh occurs during the command.'''
		kwargs["expiry"] = "315360000" # Cache expires in 10 year's time...
		kwargs["refresh-exclude"] = ".*" # Don't refresh things that don't exist in the cache at all
		return self._call(*vargs, **kwargs)

	def _fix_blank_search(self, **kwargs):
		if "channel" in kwargs and not kwargs["channel"]:
			kwargs["exclude-channel"] = ".+"
			kwargs["channel"] = ".*"
		if "category" in kwargs and not kwargs["category"]:
			kwargs["exclude-category"] = ".+"
			kwargs["category"] = ".*"
		return kwargs


	def get_filters_and_blanks(self, filter_type, search=None, type="all", channel=".*", category=".*", version=".*"):
		filters = {
			"normal": self.get_filters(filter_type, search, type, channel, category, version),
			"missing": self.count_missing_attrib(filter_type, search, type, channel, category, version)
		}
		def complete_filters(filters):
			if filters["missing"] > 0:
				filters["normal"].insert(0, "")
			return filters["normal"]
		return PendingResult.all(**filters).translate(complete_filters)

	def get_filters(self, filter_type, search=None, type="all", channel=".*", category=".*", version=".*"):
		if filter_type == "category":
			filter_type = "categories"
		if filter_type == "version":
			filter_type = "versions"
		fixed_filtering = self._fix_blank_search(type=type, channel=channel, category=category, version=version)
		filters = self._call_no_refresh(*([search] if search else []), list=filter_type, long="", **fixed_filtering)
		available_filters = filters.translate(lambda fs: list(parse_listings(fs)))
		if filter_type == "versions":
			return available_filters.translate(parse_versions)
		else:
			return available_filters

	def count_missing_attrib(self, blankattrib, search=None, type="all", channel=".*", category=".*", version=".*"):
		'''Counts the number of programmes with the given attribute blank, but that fit the other filters.'''
		if blankattrib == "type" or blankattrib == "version":
			return PendingResult.constant(0) # Don't have an option to exclude these, but I don't think you can have blank types
		exclude = {}
		exclude["exclude-"+blankattrib] = ".+"
		blank = self._call_no_refresh(*([search] if search else []), long="", type=type, channel=channel, category=category, **exclude)
		return blank.translate(parse_match_count)

	def get_episodes(self, search=None, type="all", channel=".*", category=".*", version=".*"):
		fixed_filtering = self._fix_blank_search(type=type, channel=channel, category=category, version=version)
		episodes = self._call_no_refresh(*([search] if search else []), long="", tree="", listformat="<index>: (<episodenum>) <episode>", **fixed_filtering)
		return episodes.translate(parse_episodes)

	def get_programme_info(self, index, availableversions=None):
		'''
		Need one or more of availableversions to be available for this programme or we get incomplete info.
		If they are not provided then we fetch them first.
		'''
		if availableversions is None:
			return self._version_result.then(lambda vs: self.get_programme_info(index, vs))
		info = self._call_no_refresh(index, info="", versions=",".join(availableversions))
		return info.translate(lambda i: parse_info(i, availableversions))

	def get_stream_info(self, index, version):
		info = self._call_no_refresh(index, version=version, streaminfo="")
		return info.translate(parse_streaminfo)

	def get_programme_info_and_streams(self, index, availableversions=None):
		maininfo = self.get_programme_info(index, availableversions)
		def get_info_and_version_streams(info):
			versions = info.get("versions", "").split(",")
			versionstreams = {
				version: self.get_stream_info(index, version)
				for version in versions
				if version
			}
			return PendingResult.all(**versionstreams).translate(lambda vs: dict(info, streams=vs))
		return maininfo.then(get_info_and_version_streams)

	def record_programme(self, index, displayname=None, version="default", mode="best"):
		if displayname is None:
			displayname = "Programme %s" % index
		self.recordings[index] = (displayname, version, mode)
		recording = self._call_no_refresh(index, output=self.output_location, get="", q="", versions=version, modes=mode)
		recording.on_complete(lambda _: self.recordings.pop(index, None))
		return recording

	def get_history(self, guess_version=True):
		history = self._call_no_refresh(history="", skipdeleted="", listformat="(<index>):(<name>):(<episode>):(<versions>):(<mode>):(<filename>)")
		return history.translate(lambda h: list(parse_history(h, guess_version)))

	def stream_programme_to_external(self, index, version="default", mode="best", stream_cmd="totem fd://0 --no-existing-session"):
		'''Stream a program to an external program's stdin.'''
		return self._call_no_refresh(index, versions=version, modes=mode, stream="", player=stream_cmd, q="")

	def stream_programme_to_pipe(self, index, version="default", mode="best"):
		'''Stream a program to a pipe, and return Totem's file descriptor for it.'''
		rfd, wfd = os.pipe()
		self._call_stream(wfd, index, versions=version, modes=mode, stream="", q="")
		return rfd

	def get_subtitles(self, index, version="default"):
		'''Download subtitles for a program, returning a pending result for the output location or None if there were no subtitles.'''
		st = self._call_no_refresh(index, output=self.output_location, get="", **{"subtitles-only": ""})
		return st.translate(parse_subtitles)

	def refresh_cache(self, full, *types):
		'''Complete refresh of the cache for every type given, or all if no types given.'''
		if not types:
			types = ["all"]
		typestr = ",".join(types)
		kwargs = {}
		if full:
			kwargs["refresh"] = ""
		return self._call(list="categories", q="", type=typestr, **kwargs)

class ProcessLimiter(object):
	'''Limits the number of concurrent processes by killing old ones.'''

	def __init__(self, max_processes):
		self.max_processes = max_processes
		self.pids = []
		self.processes = {}

	def add_process(self, proc):
		self.pids.append(proc.pid)
		self.processes[proc.pid] = proc
		if len(self.pids) > self.max_processes:
			removed = self.pids.pop(0)
			removed_proc = self.processes[removed]
			del self.processes[removed]
			self._kill(removed_proc)

	def _kill(self, proc):
		if proc.poll() is None:
			try:
				os.killpg(proc.pid, signal.SIGKILL)
			except OSError as exc:
				sys.stderr.write("Could not terminate process %s" % (proc.pid,))

	def close(self):
		'''Close the open streams in this limiter.'''
		for proc in self.processes.values():
			self._kill(proc)
		self.pids = []
		self.processes = {}

MAIN_STREAM_LIMITER = ProcessLimiter(1)

'''
Interfaces with the getiplayer program to query program listings and download etc.
'''

import sys
import re
import threading
import subprocess

RE_LISTING_ENTRY = re.compile(r"^(.*) \((\d*)\)$", re.MULTILINE)
RE_MATCH_TOTAL = re.compile(r"^INFO: (\d.*) Matching Programmes$", re.MULTILINE)

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

	def _call(self, *vargs, **kwargs):
		args = [self.location]
		args.extend(vargs)
		for k, v in kwargs.iteritems():
			k = "-" + k + " " if len(k) is 1 else "--" + k + "="
			args.append(k+v)
		proc = subprocess.Popen(args, stdout=subprocess.PIPE)
		def get_result():
			stdout, stderr = proc.communicate()
			#print stderr - should already be happening
			return stdout
			
		return PendingResult(lambda: proc.poll() is not None, get_result)

	def get_filters_and_blanks(self, filter_type, type="all", channel=".*", category=".*"):
		normal_filters = self.get_filters(filter_type, type, channel, category)
		missing_filters = self.count_missing_attrib(filter_type, type, channel, category)
		def complete_filters():
			filters = normal_filters.get_result()
			if missing_filters.get_result() > 0:
				filters.insert(0, "")
			return filters
		return PendingResult(lambda: normal_filters.has_result() and missing_filters.has_result(), complete_filters)

	def get_filters(self, filter_type, type="all", channel=".*", category=".*"):
		if filter_type == "category":
			filter_type = "categories"
		filters = self._call(list=filter_type, type=type, channel=channel, category=category)
		return filters.translate(lambda fs: list(parse_listings(fs)))

	def count_missing_attrib(self, blankattrib, type="all", channel=".*", category=".*"):
		'''Counts the number of programmes with the given attribute blank, but that fit the other filters.'''
		if blankattrib == "type":
			return PendingResult(lambda: True, lambda: 0) # Don't have an option to exclude these, but I don't think you can have blank types
		exclude = {}
		exclude["exclude-"+blankattrib] = ".+"
		blank = self._call(type=type, channel=channel, category=category, **exclude)
		return blank.translate(lambda bs: parse_match_count(bs))

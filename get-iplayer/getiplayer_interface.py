'''
Interfaces with the getiplayer program to query program listings and download etc.
'''

import sys
import re
import threading
import subprocess

RE_LISTING_ENTRY = re.compile(r"^(.*) \((\d*)\)$", re.MULTILINE)

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

	def get_types(self):
		types = self._call(list="type", type="all")
		return types.translate(lambda ts: parse_listings(ts))

	def get_channels(self, type="all"):
		channels = self._call(list="channel", type=type)
		return channels.translate(lambda cs: parse_listings(cs))

	def get_categories(self, type="all", channel=".*"):
		categories = self._call(list="categories", channel=channel, type=type)
		return categories.translate(lambda cs: parse_listings(cs))

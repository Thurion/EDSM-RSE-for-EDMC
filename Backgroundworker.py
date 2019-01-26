"""
EDSM-RSE a plugin for EDMC
Copyright (C) 2019 Sebastian Bauer

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
"""

from threading import Thread

if __debug__:
    from traceback import print_exc


class BackgroundWorker(Thread):
    def __init__(self, queue, rseData):
        Thread.__init__(self)
        self.queue = queue
        self.rseData = rseData

    def run(self):
        self.rseData.initializeDictionaries()
        while True:
            task = self.queue.get()
            if not task:
                break
            else:
                task.execute()

            self.queue.task_done()
        self.queue.task_done()

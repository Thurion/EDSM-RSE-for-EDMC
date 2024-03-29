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

from threading import Thread, Timer
from BackgroundTask import TimedTask
from queue import Queue
import os
import traceback
import logging

from RseData import RseData
from config import appname
logger = logging.getLogger(f"{appname}.{os.path.basename(os.path.dirname(__file__))}")


class BackgroundWorker(Thread):
    def __init__(self, queue: Queue, rse_data: RseData, interval: int = 60 * 15):
        Thread.__init__(self)
        self.queue = queue
        self.rse_data = rse_data
        self.interval = interval  # in seconds
        self.timer = None

    def timer_task(self):
        logging.debug("TimerTask triggered.")
        self.timer = Timer(self.interval, self.timer_task)
        self.timer.daemon = True
        self.timer.start()
        self.queue.put(TimedTask(self.rse_data))

    def run(self):
        self.rse_data.initialize()
        self.timer = Timer(self.interval, self.timer_task)
        self.timer.daemon = True
        self.timer.start()
        while True:
            task = self.queue.get()
            if not task:
                break
            else:
                try:
                    task.execute()
                except Exception as e:
                    logger.exception("Exception occurred in background task {bg}.".format(bg=task.__class__.__name__))
                    traceback.print_exc()

            self.queue.task_done()

        if self.timer:
            logger.debug("Stopping RSE background timer.")
            self.timer.cancel()
            self.timer.join()
        self.queue.task_done()

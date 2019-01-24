"""
EDSM-RSE a plugin for EDMC
Copyright (C) 2017 Sebastian Bauer

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
import math


class EliteSystem(object):
    def __init__(self, id64, name, x, y, z, uncertainty=None, action=0):
        self.id = id64
        self.name = name
        self.x = x
        self.y = y
        self.z = z
        self.uncertainty = uncertainty or 0
        self.distance = 10000  # set initial value to be out of reach
        self.action = action
        self.action_text = ""

    @staticmethod
    def calculateDistance(x1, x2, y1, y2, z1, z2):
        return math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2 + (z1 - z2) ** 2)

    def updateDistanceToCurrentCommanderPosition(self, x, y, z):
        self.distance = self.calculateDistanceToCoordinates(x, y, z)

    def calculateDistanceToCoordinates(self, x2, y2, z2):
        return self.calculateDistance(self.x, x2, self.y, y2, self.z, z2)

    def removeFromProject(self, projectId):
        self.action = self.action & (~ projectId)

    def calculateDistanceToSystem(self, system2):
        return self.calculateDistanceToCoordinates(system2.x, system2.y, system2.z)

    def __str__(self):
        return "id: {id}, name: {name}, distance: {distance:,.2f}, uncertainty: {uncertainty}"\
            .format(id=self.id, name=self.name, distance=self.distance, uncertainty=self.uncertainty)

    def __repr__(self):
        return self.__str__()

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.id == other.id
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash(self.id)

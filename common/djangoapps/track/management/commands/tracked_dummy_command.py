"""
Command used for testing TrackedCommands
"""

from __future__ import absolute_import

import json

from eventtracking import tracker as eventtracker

from track.management.tracked_command import TrackedCommand


class Command(TrackedCommand):
    """A locally-defined command, for testing, that returns the current context as a JSON string."""
    def add_arguments(self, parser):
        parser.add_argument('dummy_arg')
        parser.add_argument('--key1')
        parser.add_argument('--key2')

    def handle(self, *args, **options):
        return json.dumps(eventtracker.get_tracker().resolve_context())

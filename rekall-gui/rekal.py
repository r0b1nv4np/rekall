#!/usr/bin/env python

# Rekall
# Copyright (C) 2012 Michael Cohen <scudette@gmail.com>
# Copyright 2013 Google Inc. All Rights Reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
#

__author__ = "Michael Cohen <scudette@gmail.com>"

# pylint: disable=protected-access

import logging
import pdb
import sys


from rekall import args
from rekall import config
from rekall import plugin
from rekall import session


# Import and register the core plugins
from rekall import plugins  # pylint: disable=unused-import

from rekall.ui import text


class Run(plugin.Command):
    """A plugin which runs its argument (using eval).

    Note: This plugin is only defined and available when using the main entry
    point. It is not available when Rekall is used as a library since it allows
    arbitrary code execution.
    """

    name = "run"

    @classmethod
    def args(cls, parser):
        super(Run, cls).args(parser)
        parser.add_argument("script", default="print 'hello!'",
                            help="The script to evaluate")

        parser.add_argument("--run", default=None,
                            help="A file name to run.")

    def __init__(self, script, run=None, **kwargs):
        super(Run, self).__init__(**kwargs)
        if run is not None:
            script = open(run).read()

        exec script in self.session.locals


def main(argv=None):
    # New user interactive session (with extra bells and whistles).
    user_session = session.InteractiveSession()
    user_session.session_list.append(user_session)
    text_renderer = text.TextRenderer(session=user_session)

    with text_renderer.start():
        plugin_cls, flags = args.parse_args(argv=argv,
                                            user_session=user_session)

    try:
        # Run the plugin with plugin specific args.
        user_session.RunPlugin(plugin_cls, **config.RemoveGlobalOptions(flags))
    except Exception as e:
        logging.fatal("%s. Try --debug for more information." % e)
        if getattr(flags, "debug", None):
            pdb.post_mortem(sys.exc_info()[2])
        raise
    finally:
        user_session.Flush()


if __name__ == '__main__':
    main()
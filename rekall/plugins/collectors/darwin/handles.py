# Rekall Memory Forensics
#
# Copyright 2014 Google Inc. All Rights Reserved.
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

"""
Collectors for files, handles, sockets and similar.
"""
__author__ = "Adam Sindelar <adamsh@google.com>"

from rekall.entities import collector
from rekall.entities import definitions

from rekall.plugins.collectors.darwin import common
from rekall.plugins.collectors.darwin import zones


class DarwinHandleCollector(common.DarwinEntityCollector):
    """Collects handles from fileprocs (like OS X lsof is implemented)."""

    _name = "handles"
    outputs = ["Handle",
               "MemoryObject/type=fileproc",
               "MemoryObject/type=vnode",
               "MemoryObject/type=socket"]
    collect_args = dict(processes="has component Process")
    filter_input = True

    run_cost = collector.CostEnum.HighCost

    def collect(self, hint, processes):
        manager = self.manager
        for process in processes:
            proc = process["MemoryObject/base_object"]

            for fd, fileproc, flags in proc.get_open_files():
                fg_data = fileproc.autocast_fg_data()

                # The above can return None if the data in memory is invalid.
                # There's nothing we can do about that, other than rely on
                # collector redundancy. Skip.
                if not fg_data:
                    continue

                # In addition to yielding the handle, we will also yield the
                # resource it's pointing to, because other collectors rely on
                # memory objects already being out there when they parse them
                # for resource (File/Socket/etc.) specific information.
                resource_identity = manager.identify({
                    "MemoryObject/base_object": fg_data})
                handle_identity = manager.identify({
                    "MemoryObject/base_object": fileproc})

                yield [
                    resource_identity,
                    definitions.MemoryObject(
                        base_object=fg_data,
                        type=fg_data.obj_type)]

                yield [
                    handle_identity,
                    definitions.Handle(
                        process=process.identity,
                        fd=fd,
                        flags=flags,
                        resource=resource_identity),
                    definitions.MemoryObject(
                        base_object=fileproc,
                        type="fileproc")]


class DarwinSocketZoneCollector(zones.DarwinZoneElementCollector):
    outputs = ["MemoryObject/type=socket"]
    zone_name = "socket"
    type_name = "socket"

    def validate_element(self, socket):
        return socket == socket.so_rcv.sb_so


class DarwinSocketLastAccess(common.DarwinEntityCollector):
    outputs = ["Event"]
    collect_args = dict(processes="has component Process",
                        sockets="MemoryObject/type is 'socket'")
    complete_input = True

    def collect(self, hint, processes, sockets):
        by_pid = {}
        for process in processes:
            by_pid[process["Process/pid"]] = process

        for socket in sockets:
            base_socket = socket["MemoryObject/base_object"]
            process = by_pid[base_socket.last_pid]
            if not process:
                continue

            event_identity = self.manager.identify({
                ("Event/actor", "Event/action", "Event/target",
                 "Event/category"):
                (process.identity, "accessed", socket.identity, "latest")})
            yield [
                event_identity,
                definitions.Event(
                    actor=process.identity,
                    action="accessed",
                    target=socket.identity,
                    category="latest")]


class DarwinSocketCollector(common.DarwinEntityCollector):
    """Searches for all memory objects that are sockets and parses them."""

    _name = "sockets"
    outputs = [
        "Connection",
        "OSILayer3",
        "OSILayer4",
        "Socket",
        "Handle",
        "Event",
        "Timestamps",
        "File/type=socket",
        "MemoryObject/type=vnode"]

    collect_args = dict(sockets="MemoryObject/type is 'socket'")

    filter_input = True

    def collect(self, hint, sockets):
        for entity in sockets:
            socket = entity["MemoryObject/base_object"]
            family = str(socket.addressing_family)

            if family in ("AF_INET", "AF_INET6"):
                yield [
                    entity.identity,
                    definitions.Named(
                        name=socket.human_name,
                        kind="IP Connection"),
                    definitions.Connection(
                        protocol_family=family.replace("AF_", "")),
                    definitions.OSILayer3(
                        src_addr=socket.src_addr,
                        dst_addr=socket.dst_addr,
                        protocol="IPv4" if family == "AF_INET" else "IPv6"),
                    definitions.OSILayer4(
                        src_port=socket.src_port,
                        dst_port=socket.dst_port,
                        protocol=socket.l4_protocol,
                        state=socket.tcp_state)]
            elif family == "AF_UNIX":
                if socket.vnode:
                    path = socket.vnode.full_path
                    file_identity = self.session.entities.identify({
                        "File/path": path})
                else:
                    path = None
                    file_identity = None

                yield [
                    entity.identity,
                    definitions.Named(
                        name=socket.human_name,
                        kind="Unix Socket"),
                    definitions.Connection(
                        protocol_family="UNIX"),
                    definitions.Socket(
                        type=socket.unix_type,
                        file=file_identity,
                        address="0x%x" % int(socket.so_pcb),
                        connected="0x%x" % int(socket.unp_conn))]

                # There may be a vnode here - if so, yield it.
                if path:
                    yield [
                        definitions.File(
                            path=path,
                            type="socket"),
                        definitions.Named(
                            name=path,
                            kind="Socket"),
                        definitions.MemoryObject(
                            base_object=socket.vnode.deref(),
                            type="vnode")]
            else:
                yield [
                    entity.identity,
                    definitions.Named(
                        kind="Unknown Socket"),
                    definitions.Connection(
                        protocol_family=family.replace("AF_", ""))]


class DarwinFileCollector(common.DarwinEntityCollector):
    """Collects files based on vnodes."""

    outputs = ["File", "Permissions", "Timestamps", "Named"]
    _name = "files"
    collect_args = dict(vnodes="MemoryObject/type is 'vnode'")
    filter_input = True

    def collect(self, hint, vnodes):
        manager = self.manager
        for entity in vnodes:
            vnode = entity["MemoryObject/base_object"]
            path = vnode.full_path

            components = [entity.identity,
                          definitions.File(
                              path=path),
                          definitions.Named(
                              name=path,
                              kind="File")]

            # Parse HFS-specific metadata. We could look at the mountpoint and
            # see if the filesystem is actually HFS, but it turns out that
            # cnodes are also used for stuff like the dev filesystem, so let's
            # just try and see if there's one that looks valid and go with it.
            cnode = vnode.v_data.dereference_as("cnode")
            if cnode.c_rwlock == cnode:
                cattr = vnode.v_data.dereference_as("cnode").c_attr

                # HFS+ stores timestamps as UTC.
                components.append(definitions.Timestamps(
                    created_at=cattr.ca_ctime.as_datetime(),
                    modified_at=cattr.ca_mtime.as_datetime(),
                    accessed_at=cattr.ca_atime.as_datetime(),
                    backup_at=cattr.ca_btime.as_datetime()))

            posix_uid = vnode.v_cred.cr_posix.cr_ruid
            if posix_uid and posix_uid != 0:
                components.append(definitions.Permissions(
                    owner=manager.identify({
                        "User/uid": posix_uid})))

            yield components


class UnpListCollector(common.DarwinEntityCollector):
    """Walks the global unpcb lists and returns the unix sockets.

    See here:
        github.com/opensource-apple/xnu/blob/10.9/bsd/kern/uipc_usrreq.c#L121
    """

    outputs = ["MemoryObject/type=socket", "Named/kind=Unix Socket"]

    def collect(self, hint):
        for head_const in ["_unp_dhead", "_unp_shead"]:
            lhead = self.session.get_constant_object(
                head_const,
                target="unp_head")

            for unp in lhead.lh_first.walk_list("unp_link.le_next"):
                yield [
                    definitions.MemoryObject(
                        base_object=unp.unp_socket,
                        type="socket"),
                    definitions.Named(
                        kind="Unix Socket")]

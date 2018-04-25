#
#  Copyright 2018 Red Hat | Ansible
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import absolute_import, division, print_function

import copy

from ansible.module_utils.k8s.helper import COMMON_ARG_SPEC, AUTH_ARG_SPEC, OPENSHIFT_ARG_SPEC
from ansible.module_utils.k8s.common import KubernetesAnsibleModule

from dictdiffer import diff

try:
    from kubernetes.client.rest import ApiException
except ImportError:
    # Exception handled in common
    pass


class KubernetesRawModule(KubernetesAnsibleModule):

    def __init__(self, *args, **kwargs):
        self.client = None

        mutually_exclusive = [
            ('resource_definition', 'src'),
        ]

        KubernetesAnsibleModule.__init__(self, *args,
                                         mutually_exclusive=mutually_exclusive,
                                         supports_check_mode=True,
                                         **kwargs)

        self.kind = self.params.pop('kind')
        self.api_version = self.params.pop('api_version')
        self.resource_definition = self.params.pop('resource_definition')
        self.src = self.params.pop('src')
        if self.src:
            self.resource_definitions = self.load_resource_definitions(self.src)

        if isinstance(self.resource_definition, dict):
            if self.resource_definition:
                self.api_version = self.resource_definition.get('apiVersion')
                self.kind = self.resource_definition.get('kind')

            if not self.api_version:
                self.fail_json(
                    msg=("Error: no api_version specified. Use the api_version parameter, or provide it as part of a ",
                        "resource_definition.")
                )
            if not self.kind:
                self.fail_json(
                    msg="Error: no kind specified. Use the kind parameter, or provide it as part of a resource_definition"
                )

            self.resource_definitions = [self.resource_definition]

    @property
    def argspec(self):
        argspec = copy.deepcopy(COMMON_ARG_SPEC)
        argspec.update(copy.deepcopy(AUTH_ARG_SPEC))
        return argspec

    def exact_match(definition):
        def inner(resource):
            return all([
                resource.kind == definition.get('kind'),
                '/'.join([resource.group, resource.apiversion]) == definition.get('apiVersion')
            ])
        return inner

    def execute_module(self):
        self.client = self.get_api_client()
        for definition in self.resource_definitions:
            resource = self.client.search_resources(self.exact_match(definition))
            self.perform_action(resource, definition)

    def perform_action(self, resource, definition):

        state = self.params.pop('state', None)
        force = self.params.pop('force', False)
        name = definition.get('metadata', {}).get('name') or self.params.get('name')
        namespace = definition.get('metadata', {}).get('namespace') or self.params.get('namespace')
        existing = None
        return_attributes = dict(changed=False, result=dict())

        self.remove_aliases()

        if definition['kind'].endswith('list'):
            k8s_obj = resource.list(namespace=self.params.get('namespace'))
            return_attributes['result'] = k8s_obj.to_dict()
            self.exit_json(**return_attributes)

        try:
            existing = resource.get(name=name, namespace=namespace)
        except ApiException as exc:
            if exc.status != 404:
                self.fail_json(msg='Failed to retrieve requested object: {0}'.format(exc.body),
                            error=exc.status)

        if state == 'absent':
            if not existing:
                # The object already does not exist
                self.exit_json(**return_attributes)
            else:
                # Delete the object
                if not self.check_mode:
                    try:
                        resource.delete(name, namespace=namespace)
                    except ApiException as exc:
                        self.fail_json(msg="Failed to delete object: {0}".format(exc.body),
                                       error=exc.status)
                return_attributes['changed'] = True
                self.exit_json(**return_attributes)
        else:
            if not existing:
                if not self.check_mode:
                    k8s_obj = resource.create(definition, namespace=namespace)
                    return_attributes['result'] = k8s_obj.to_dict()
                return_attributes['changed'] = True
                self.exit_json(**return_attributes)

            if existing and force:
                if not self.check_mode:
                    try:
                        k8s_obj = resource.replace(definition, name=name, namespace=namespace)
                    except ApiException as exc:
                        self.fail_json(msg="Failed to replace object: {0}".format(exc.body),
                                       error=exc.status)
                return_attributes['result'] = k8s_obj.to_dict()
                return_attributes['changed'] = True
                self.exit_json(**return_attributes)

            existing_attrs = existing.to_dict()
            shared_attrs = {}
            for k in definition.keys():
                shared_attrs[k] = existing_attrs.get(k)
            diffs = list(diff(definition, shared_attrs))
            match = len(diffs) == 0

            if match:
                return_attributes['result'] = existing_attrs
                self.exit_json(**return_attributes)
            # Differences exist between the existing obj and requested params
            if not self.check_mode:
                try:
                    k8s_obj = resource.update(definition, name=name, namespace=namespace)
                except ApiException as exc:
                    self.fail_json(msg="Failed to patch object: {0}".format(exc.body))
            return_attributes['result'] = k8s_obj.to_dict()
            return_attributes['changed'] = True
            self.exit_json(**return_attributes)

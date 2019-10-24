from __future__ import print_function, unicode_literals

import logging
import re

import texttable as tt
try:
    from backports.shutil_get_terminal_size import get_terminal_size
except ImportError:
    from shutil import get_terminal_size

from rbtools.commands import Command, Option
from rbtools.utils.repository import get_repository_id
from rbtools.utils.users import get_username


DRAFT_STATE = 'Draft'
OPEN_ISSUES_STATE = 'Open Issues'
PENDING_STATE = 'Pending'
SHIPIT_STATE = 'Ship It!'

STATUSES = (
    DRAFT_STATE,
    OPEN_ISSUES_STATE,
    PENDING_STATE,
    SHIPIT_STATE,
)


class Status(Command):
    """Display review requests for the current repository."""


    name = 'status'
    author = 'The Review Board Project'
    description = 'Output a list of your pending review requests.'
    args = '[review-request [revision]]'
    option_list = [
        Option('--format',
               dest='format',
               default=None,
               help='Set the output format. The format is in the form of '
                    '%%(field_name)s, where field_name is one of: id, status,'
                    'summary, or description.\n'
                    'A character escape can be included via \\xXX where XX is '
                    'the hex code of a character.\n'
                    'For example: --format="%%(id)s\\x09%%(summary)s"\n'
                    'This option will print out the id and summary tab-'
                    'separated.'),
        Option('-z',
               dest='format_nul',
               default=False,
               action='store_true',
               help='Null-terminate each entry. Otherwise, the entries will '
                    'be newline-terminated.'),
        Option('--all',
               dest='all_repositories',
               action='store_true',
               default=False,
               help='Shows review requests for all repositories instead '
                    'of just the detected repository.'),
        Option('--status-filter',
               dest='status_filter',
               default=[],
               choices=STATUSES,
               action='append',
               help='Status(es) to filter when listing review states. '
                    'Defaults to all statuses.'),
        Command.server_options,
        Command.repository_options,
        Command.perforce_options,
        Command.tfs_options,
    ]
    # The number of spaces between the request's status and the request's id
    # and summary.
    TAB_SIZE = 3
    # The number of spaces after the end of the request's summary.
    PADDING = 5

    _HEX_RE = re.compile(r'\\x([0-9a-fA-f]{2})')

    def tabulate(self, review_requests):
        """Print review request summary and status in a table.

        Args:
            review_requests (list of dict):
                A list that contains statistics about each review request.
        """
        if len(review_requests):
            has_branches = False
            has_bookmarks = False

            table = tt.Texttable(get_terminal_size().columns)
            header = ['Status', 'Review Request']

            for info in review_requests:
                if 'branch' in info:
                    has_branches = True

                if 'bookmark' in info:
                    has_bookmarks = True

            if has_branches:
                header.append('Branch')

            if has_bookmarks:
                header.append('Bookmark')

            table.header(header)

            for info in review_requests:
                row = [
                    info['status'],
                    'r/%s - %s' % (info['id'], info['summary']),
                ]

                if has_branches:
                    row.append(info.get('branch') or '')

                if has_bookmarks:
                    row.append(info.get('bookmark') or '')

                table.add_row(row)

            print(table.draw())
        else:
            print('No review requests found.')

        print()

    def get_data(self, requests, status_filter=None):
        """Return current status and review summary for all reviews.

        Args:
            requests (ListResource):
                A ListResource that contains data on all open/draft requests.
            status_filter (list of str):
                Statuses to filter on. See also: `STATUSES`.

        Returns:
            list: A list whose elements are dicts of each request's statistics.
        """
        requests_stats = []
        status_filter = status_filter or STATUSES

        for request in requests.all_items:
            status = ''
            if request.issue_open_count or request.ship_it_count:
                # Non-mutually exclusive states.
                #
                # - One can have ship-it's, as well as fix-it's in a single CR.
                status_map = {
                    OPEN_ISSUES_STATE: request.issue_open_count,
                    SHIPIT_STATE: request.ship_it_count,
                }

                status = '; '.join(
                    '%s (%s)' % (status_name, count)
                    for status_name, count in status_map.items()
                    if count and status_name in status_filter
                )
            else:
                # Mutually exclusive states.

                if request.draft:
                    status = DRAFT_STATE
                else:
                    status = PENDING_STATE

                if status not in status_filter:
                    status = ''

            if not status:  # Status was filtered out.
                continue

            info = {
                'id':  request.id,
                'status': status,
                'summary': request.summary,
                'description': request.description,
            }

            if 'local_branch' in request.extra_data:
                info['branch'] = \
                    request.extra_data['local_branch']
            elif 'local_bookmark' in request.extra_data:
                info['bookmark'] = \
                    request.extra_data['local_bookmark']

            requests_stats.append(info)

        return requests_stats

    def main(self):
        repository_info, tool = self.initialize_scm_tool(
            client_name=self.options.repository_type)
        server_url = self.get_server_url(repository_info, tool)
        api_client, api_root = self.get_api(server_url)
        self.setup_tool(tool, api_root=api_root)
        username = get_username(api_client, api_root, auth_required=True)

        # Check if repository info on reviewboard server match local ones.
        repository_info = repository_info.find_server_repository_info(api_root)

        status_filter = set(self.options.status_filter) or STATUSES

        query_args = {
            'from_user': username,
            'status': 'pending',
            'expand': 'draft',
        }

        if not self.options.all_repositories:
            repo_id = get_repository_id(
                repository_info,
                api_root,
                repository_name=self.options.repository_name)

            if repo_id:
                query_args['repository'] = repo_id
            else:
                logging.warning('The repository detected in the current '
                                'directory was not found on\n'
                                'the Review Board server. Displaying review '
                                'requests from all repositories.')

        review_requests = api_root.get_review_requests(**query_args)
        review_request_info = self.get_data(
            review_requests, status_filter=status_filter)

        if self.options.format:
            self.format_results(review_request_info)
        else:
            self.tabulate(review_request_info)

    def format_results(self, review_requests):
        """Print formatted information about the review requests.

        Args:
            review_requests (list of dict):
                The information about the review requests.
        """
        fmt = self._HEX_RE.sub(
            lambda m: chr(int(m.group(1), 16)),
            self.options.format,
        )

        if self.options.format_nul:
            end = '\x00'
        else:
            end = '\n'

        for info in review_requests:
            print(fmt % info, end=end)

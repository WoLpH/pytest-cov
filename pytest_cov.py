"""Coverage plugin for pytest."""

import os

import pytest

import cov_core
import cov_core_init


class CoverageError(Exception):
    '''Indicates that our coverage is too low'''


def pytest_addoption(parser):
    """Add options to control coverage."""

    group = parser.getgroup('coverage reporting with distributed testing '
                            'support')
    group.addoption('--cov', action='append', default=[], metavar='path',
                    dest='cov_source',
                    help='measure coverage for filesystem path '
                    '(multi-allowed)')
    group.addoption('--cov-report', action='append', default=[],
                    metavar='type', dest='cov_report',
                    choices=['term', 'term-missing', 'annotate', 'html',
                             'xml', ''],
                    help='type of report to generate: term, term-missing, '
                    'annotate, html, xml (multi-allowed)')
    group.addoption('--cov-config', action='store', default='.coveragerc',
                    metavar='path', dest='cov_config',
                    help='config file for coverage, default: .coveragerc')
    group.addoption('--no-cov-on-fail', action='store_true', default=False,
                    dest='no_cov_on_fail',
                    help='do not report coverage if test run fails, '
                         'default: False')
    group.addoption('--cov-min', action='store', metavar='MIN', type='int',
                    help='Fail if the total coverage is less than MIN.')



@pytest.mark.tryfirst
def pytest_load_initial_conftests(early_config, parser, args):
    ns = parser.parse_known_args(args)
    if ns.cov_source:
        plugin = CovPlugin(ns, early_config.pluginmanager)
        early_config.pluginmanager.register(plugin, '_cov')


def pytest_configure(config):
    """Activate coverage plugin if appropriate."""
    if config.getvalue('cov_source'):
        if not config.pluginmanager.hasplugin('_cov'):
            plugin = CovPlugin(config.option, config.pluginmanager,
                               start=False)
            config.pluginmanager.register(plugin, '_cov')


class CovPlugin(object):
    """Use coverage package to produce code coverage reports.

    Delegates all work to a particular implementation based on whether
    this test process is centralised, a distributed master or a
    distributed slave.
    """

    def __init__(self, options, pluginmanager, start=True):
        """Creates a coverage pytest plugin.

        We read the rc file that coverage uses to get the data file
        name.  This is needed since we give coverage through it's API
        the data file name.
        """

        # Our implementation is unknown at this time.
        self.pid = None
        self.cov = None
        self.cov_controller = None
        self.failed = False
        self.options = options

        is_dist = (getattr(options, 'numprocesses', False) or
                   getattr(options, 'distload', False) or
                   getattr(options, 'dist', 'no') != 'no')
        if is_dist and start:
            self.start(cov_core.DistMaster)
        elif start:
            self.start(cov_core.Central)

        # slave is started in pytest hook

    def start(self, controller_cls, config=None, nodeid=None):
        if config is None:
            # fake config option for cov_core
            class Config(object):
                option = self.options

            config = Config()

        self.cov_controller = controller_cls(
            self.options.cov_source,
            self.options.cov_report or ['term'],
            self.options.cov_config,
            config,
            nodeid
        )
        self.cov_controller.start()

    def pytest_sessionstart(self, session):
        """At session start determine our implementation and delegate to it."""
        self.pid = os.getpid()
        is_slave = hasattr(session.config, 'slaveinput')
        if is_slave:
            nodeid = session.config.slaveinput.get('slaveid',
                                                   getattr(session, 'nodeid'))
            self.start(cov_core.DistSlave, session.config, nodeid)

    def pytest_configure_node(self, node):
        """Delegate to our implementation.

        Mark this hook as optional in case xdist is not installed.
        """
        self.cov_controller.configure_node(node)
    pytest_configure_node.optionalhook = True

    def pytest_testnodedown(self, node, error):
        """Delegate to our implementation.

        Mark this hook as optional in case xdist is not installed.
        """
        self.cov_controller.testnodedown(node, error)
    pytest_testnodedown.optionalhook = True

    def pytest_sessionfinish(self, session, exitstatus):
        """Delegate to our implementation."""
        self.failed = exitstatus != 0
        if self.cov_controller is not None:
            self.cov_controller.finish()

    def pytest_terminal_summary(self, terminalreporter):
        """Delegate to our implementation."""
        if self.cov_controller is None:
            return
        if not (self.failed and self.options.no_cov_on_fail):
            total = self.cov_controller.summary(terminalreporter._tw)
            if total < self.options.cov_min:
                raise CoverageError(('Required test coverage of %d%% not '
                                     'reached. Total coverage: %.2f%%')
                                    % (self.options.cov_min, total))

    def pytest_runtest_setup(self, item):
        if os.getpid() != self.pid:
            # test is run in another process than session, run
            # coverage manually
            self.cov = cov_core_init.init()

    def pytest_runtest_teardown(self, item):
        if self.cov is not None:
            cov_core.multiprocessing_finish(self.cov)
            self.cov = None


def pytest_funcarg__cov(request):
    """A pytest funcarg that provides access to the underlying coverage
    object.
    """

    # Check with hasplugin to avoid getplugin exception in older pytest.
    if request.config.pluginmanager.hasplugin('_cov'):
        plugin = request.config.pluginmanager.getplugin('_cov')
        if plugin.cov_controller:
            return plugin.cov_controller.cov
    return None

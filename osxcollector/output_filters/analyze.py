#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# The AnalyzeFilter is a handy little tool that ties together many filters to attempt to
# enhance the output of OSXCollector with data from threat APIs, compare against blacklists,
# search for lines related to suspicious domains, ips, or files, and generally figure shit out.
#
# The more detailed description of what goes on:
#  1. Parse out browser extension information.
#  2. Find all the domains in every line. Add them to the output lines.
#  3. Find any file hashes or domains that are on blacklists. Mark those lines.
#  4. Take any filepaths from the command line and mark all lines related to those.
#  5. Take any domain or IP from the command line and use OpenDNS Investigate API to find all the domains
#     related to those domains and all the domains related to those related domains - basically the 1st and 2nd
#     generation related domains. Mark any lines where these domains appear.
#  6. Lookup all sha1 hashes in ShadowServer's bin-test whitelist. Files that match both hash and filename are ignored by further filters.
#  7. Lookup file hashes in VirusTotal and mark any lines with suspicious files hashes.
#  8. Lookup all the domains in the file with OpenDNS Investigate. Categorize and score the domains.
#     Mark all the lines that contain domains that were scored as "suspicious".
#  9. Lookup suspicious domains, those domains on a blacklist, or those related to the initial input in VirusTotal.
# 10. Cleanup the browser history and sort it in descending time order.
# 11. Save all the enhanced output to a new file.
# 12. Look at all the interesting lines in the file and try to summarize them in some very human readable output.
# 13. Party!
#
import sys
from argparse import ArgumentParser
from numbers import Number

from osxcollector.output_filters.base_filters.chain import ChainFilter
from osxcollector.output_filters.base_filters.output_filter import OutputFilter
from osxcollector.output_filters.base_filters.output_filter import run_filter_main
from osxcollector.output_filters.chrome.find_extensions import FindExtensionsFilter as ChromeExtensionsFilter
from osxcollector.output_filters.chrome.sort_history import SortHistoryFilter as ChromeHistoryFilter
from osxcollector.output_filters.find_blacklisted import FindBlacklistedFilter
from osxcollector.output_filters.find_domains import FindDomainsFilter
from osxcollector.output_filters.firefox.find_extensions import FindExtensionsFilter as FirefoxExtensionsFilter
from osxcollector.output_filters.firefox.sort_history import SortHistoryFilter as FirefoxHistoryFilter
from osxcollector.output_filters.opendns.lookup_domains import LookupDomainsFilter as OpenDnsLookupDomainsFilter
from osxcollector.output_filters.opendns.related_domains import RelatedDomainsFilter as OpenDnsRelatedDomainsFilter
from osxcollector.output_filters.related_files import RelatedFilesFilter
from osxcollector.output_filters.shadowserver.lookup_hashes import LookupHashesFilter as ShadowServerLookupHashesFilter
from osxcollector.output_filters.virustotal.lookup_domains import LookupDomainsFilter as VtLookupDomainsFilter
from osxcollector.output_filters.virustotal.lookup_hashes import LookupHashesFilter as VtLookupHashesFilter


class AnalyzeFilter(ChainFilter):

    """AnalyzeFilter chains all the other filters to produce maximum effect.

    A lot of the smarts of AnalyzeFilter are around what filters to run in which order and how results of one filter should
    effect the operations of the next filter.
    """

    def __init__(self, no_opendns=False, no_virustotal=False, no_shadowserver=False, readout=False, html=False, **kwargs):

        filter_chain = []

        if not readout:
            filter_chain.append(ChromeExtensionsFilter(**kwargs))
            filter_chain.append(FirefoxExtensionsFilter(**kwargs))

            filter_chain.append(FindDomainsFilter(**kwargs))

            # Do hash related lookups first. This is done first since hash lookup is not influenced
            # by anything but other hash lookups.
            if not no_shadowserver:
                filter_chain.append(ShadowServerLookupHashesFilter(**kwargs))
            if not no_virustotal:
                filter_chain.append(VtLookupHashesFilter(lookup_when=AnalyzeFilter.lookup_when_not_in_shadowserver, **kwargs))

            # Find blacklisted stuff next. Finding blacklisted domains requires running FindDomainsFilter first.
            filter_chain.append(FindBlacklistedFilter(**kwargs))

            # RelatedFilesFilter and OpenDnsRelatedDomainsFilter use command line args in addition to previous filter results to find
            # lines of interest.
            filter_chain.append(RelatedFilesFilter(when=AnalyzeFilter.find_related_when, **kwargs))
            if not no_opendns:
                filter_chain.append(OpenDnsRelatedDomainsFilter(related_when=AnalyzeFilter.find_related_when, **kwargs))

            # Lookup threat info on suspicious and related stuff
            if not no_opendns:
                filter_chain.append(OpenDnsLookupDomainsFilter(lookup_when=AnalyzeFilter.lookup_when_not_in_shadowserver, **kwargs))
            if not no_virustotal:
                filter_chain.append(VtLookupDomainsFilter(lookup_when=AnalyzeFilter.lookup_domains_in_vt_when, **kwargs))

            # Sort browser history for maximum pretty
            filter_chain.append(FirefoxHistoryFilter(**kwargs))
            filter_chain.append(ChromeHistoryFilter(**kwargs))

        if html:
            filter_chain.append(_HtmlSummaryFilter(**kwargs))
        else:
            filter_chain.append(_VeryReadableOutputFilter(**kwargs))

        super(AnalyzeFilter, self).__init__(filter_chain, **kwargs)

    def _on_get_argument_parser(self):
        """Returns an ArgumentParser with arguments for just this OutputFilter (not the contained chained OutputFilters).

        Returns:
            An `argparse.ArgumentParser`
        """
        parser = ArgumentParser()
        group = parser.add_argument_group('AnalyzeFilter')
        group.add_argument('--readout', dest='readout', action='store_true', default=False,
                           help='[OPTIONAL] Skip the analysis and just output really readable analysis')
        group.add_argument('--no-opendns', dest='no_opendns', action='store_true', default=False,
                           help='[OPTIONAL] Don\'t run OpenDNS filters')
        group.add_argument('--no-virustotal', dest='no_virustotal', action='store_true', default=False,
                           help='[OPTIONAL] Don\'t run VirusTotal filters')
        group.add_argument('--no-shadowserver', dest='no_shadowserver', action='store_true', default=False,
                           help='[OPTIONAL] Don\'t run ShadowServer filters')
        group.add_argument('-M', '--monochrome', dest='monochrome', action='store_true', default=False,
                           help='[OPTIONAL] Output monochrome analysis')
        group.add_argument('--show-signature-chain', dest='show_signature_chain', action='store_true', default=False,
                           help='[OPTIONAL] Output unsigned startup items and kexts.')
        group.add_argument('--show-browser-ext', dest='show_browser_ext', action='store_true', default=False,
                           help='[OPTIONAL] Output the list of installed browser extensions.')
        group.add_argument('--html', dest='html', action='store_true', default=False,
                           help='[OPTIONAL] Print summary in HTML format.')
        return parser

    @staticmethod
    def include_in_summary(blob):
        _KEYS_FOR_SUMMARY = [
            'osxcollector_vthash',
            'osxcollector_vtdomain',
            'osxcollector_opendns',
            'osxcollector_blacklist',
            'osxcollector_related'
        ]

        return any([key in blob for key in _KEYS_FOR_SUMMARY])

    @staticmethod
    def lookup_when_not_in_shadowserver(blob):
        """ShadowServer whitelists blobs that can be ignored."""
        return 'osxcollector_shadowserver' not in blob

    @staticmethod
    def lookup_domains_in_vt_when(blob):
        """VT domain lookup is a final step and what to lookup is dependent upon what has been found so far."""
        return AnalyzeFilter.lookup_when_not_in_shadowserver(blob) and AnalyzeFilter.include_in_summary(blob)

    @staticmethod
    def find_related_when(blob):
        """When to find related terms or domains.

        Stuff in ShadowServer is not interesting.
        Blacklisted file paths are worth investigating.
        Files where the md5 could not be calculated are also interesting. Root should be able to read files.
        Files with a bad hash in VT are obviously malware, go find related bad stuff.

        Args:
            blob - a line of output from OSXCollector
        Returns:
            boolean
        """
        if 'osxcollector_shadowserver' in blob:
            return False
        if '' == blob.get('md5', None):
            return True
        return any([key in blob for key in ['osxcollector_vthash', 'osxcollector_related']])


class _VeryReadableOutputFilter(OutputFilter):

    END_COLOR = '\033[0m'
    SECTION_COLOR = '\033[1m'
    BOT_COLOR = '\033[93m\033[1m'
    KEY_COLOR = '\033[94m'
    VAL_COLOR = '\033[32m'

    def __init__(self, monochrome=False, show_signature_chain=False, show_browser_ext=False, **kwargs):
        super(_VeryReadableOutputFilter, self).__init__(**kwargs)
        self._vthash = []
        self._vtdomain = []
        self._opendns = []
        self._blacklist = []
        self._related = []
        self._signature_chain = []
        self._extensions = []
        self._monochrome = monochrome
        self._show_signature_chain = show_signature_chain
        self._show_browser_ext = show_browser_ext

        self._add_to_blacklist = []

    def filter_line(self, blob):
        """Each Line of OSXCollector output will be passed to filter_line.

        The OutputFilter should return the line, either modified or unmodified.
        The OutputFilter can also choose to return nothing, effectively swallowing the line.

        Args:
            output_line: A dict

        Returns:
            A dict or None
        """
        if 'osxcollector_vthash' in blob:
            self._vthash.append(blob)

        if 'osxcollector_vtdomain' in blob:
            self._vtdomain.append(blob)

        if 'osxcollector_opendns' in blob:
            self._opendns.append(blob)

        if 'osxcollector_blacklist' in blob:
            self._blacklist.append(blob)

        if 'osxcollector_related' in blob:
            self._related.append(blob)

        if self._show_signature_chain:
            if 'signature_chain' in blob and blob['osxcollector_section'] in ['startup', 'kext']:
                signature_chain = blob['signature_chain']
                if not len(signature_chain) or 'Apple Root CA' != signature_chain[-1]:
                    self._signature_chain.append(blob)

        if self._show_browser_ext:
            if blob['osxcollector_section'] in ['firefox', 'chrome'] and blob.get('osxcollector_subsection') == 'extensions':
                self._extensions.append(blob)

        return blob

    def _write(self, msg, color=END_COLOR):
        if not self._monochrome:
            sys.stdout.write(color)
        try:
            sys.stdout.write(msg.encode("utf-8", errors="ignore"))
        except UnicodeDecodeError as err:
            sys.stdout.write(msg)
            sys.stderr.write('Unicode decode error: {0}'.format(err))
        if not self._monochrome:
            sys.stdout.write(self.END_COLOR)

    def end_of_lines(self):
        """Called after all lines have been fed to filter_output_line.

        The OutputFilter can do any batch processing on that requires the complete input.

        Returns:
            An array of dicts (empty array if no lines remain)
        """
        self._write('== Very Readable Output Bot ==\n', self.BOT_COLOR)
        self._write('Let\'s see what\'s up with this machine.\n\n', self.BOT_COLOR)

        if len(self._vthash):
            self._write('Dang! You\'ve got known malware on this machine. Hope it\'s commodity stuff\n', self.BOT_COLOR)
            self._summarize_blobs(self._vthash)
            self._write('Sheesh! This is why we can\'t have nice things!\n\n', self.BOT_COLOR)

        if len(self._vtdomain):
            self._write('I see you\'ve been visiting some \'questionable\' sites. If you trust VirusTotal that is.\n', self.BOT_COLOR)
            self._summarize_blobs(self._vtdomain)
            self._write('I hope it was worth it!\n\n', self.BOT_COLOR)

        if len(self._opendns):
            self._write('Well, here\'s some domains OpenDNS wouldn\'t recommend.\n', self.BOT_COLOR)
            self._summarize_blobs(self._opendns)
            self._write('You know you shouldn\'t just click every link you see? #truth\n\n', self.BOT_COLOR)

        if len(self._blacklist):
            self._write('We put stuff on a blacklist for a reason. Mostly so you don\'t do this.\n', self.BOT_COLOR)
            self._summarize_blobs(self._blacklist)
            self._write('SMH\n\n', self.BOT_COLOR)

        if len(self._related):
            self._write('This whole things started with just a few clues. Now look what I found.\n', self.BOT_COLOR)
            self._summarize_blobs(self._related)
            self._write('Nothing hides from Very Readable Output Bot\n\n', self.BOT_COLOR)

        if len(self._signature_chain):
            self._write('If these binaries were signed by \'Apple Root CA\' I\'d trust them more.\n', self.BOT_COLOR)
            self._summarize_blobs(self._signature_chain)
            self._write('Let\'s just try and stick with some safe software\n\n', self.BOT_COLOR)

        if len(self._extensions):
            self._write('Let\'s see what\'s hiding in the browser, shall we.\n', self.BOT_COLOR)
            self._summarize_blobs(self._extensions)
            self._write('You know these things have privileges galore.\n\n', self.BOT_COLOR)

        if len(self._add_to_blacklist):
            self._add_to_blacklist = list(set(self._add_to_blacklist))
            self._write('If I were you, I\'d probably update my blacklists to include:\n', self.BOT_COLOR)
            for key, val in self._add_to_blacklist:
                self._summarize_val(key, val)
            self._write('That might just help things, Skippy!\n\n', self.BOT_COLOR)

        self._write('== Very Readable Output Bot ==\n', self.BOT_COLOR)
        self._write('#kaythanksbye', self.BOT_COLOR)

        return []

    def _summarize_blobs(self, blobs):
        for blob in blobs:
            self._summarize_line(blob)

            add_to_blacklist = False

            if 'osxcollector_vthash' in blob:
                self._summarize_vthash(blob)
                add_to_blacklist = True

            if 'osxcollector_vtdomain' in blob:
                self._summarize_vtdomain(blob)

            if 'osxcollector_opendns' in blob:
                self._summarize_opendns(blob)

            if 'osxcollector_blacklist' in blob:
                for key in blob['osxcollector_blacklist'].keys():
                    self._summarize_val('blacklist-{0}'.format(key), blob['osxcollector_blacklist'][key])

            if 'osxcollector_related' in blob:
                for key in blob['osxcollector_related'].keys():
                    self._summarize_val('related-{0}'.format(key), blob['osxcollector_related'][key])

            if 'md5' in blob and '' == blob['md5']:
                add_to_blacklist = True

            if add_to_blacklist:
                blacklists = blob.get('osxcollector_blacklist', {})
                values_on_blacklist = blacklists.get('hashes', [])
                for key in ['md5', 'sha1', 'sha2']:
                    val = blob.get(key, '')
                    if len(val) and val not in values_on_blacklist:
                        self._add_to_blacklist.append((key, val))

                values_on_blacklist = blacklists.get('domains', [])
                for domain in blob.get('osxcollector_domains', []):
                    if domain not in values_on_blacklist:
                        self._add_to_blacklist.append(('domain', domain))

    def _summarize_line(self, blob):
        section = blob.get('osxcollector_section')
        subsection = blob.get('osxcollector_subsection', '')

        self._write('- {0} {1}\n'.format(section, subsection), self.SECTION_COLOR)
        for key in sorted(blob.keys()):
            if not key.startswith('osxcollector') and blob.get(key):
                val = blob.get(key)
                self._summarize_val(key, val)

    def _summarize_vthash(self, blob):
        for blob in blob['osxcollector_vthash']:
            for key in ['positives', 'total', 'scan_date', 'permalink']:
                val = blob.get(key)
                self._summarize_val(key, val, 'vthash')

    def _summarize_vtdomain(self, blob):
        for blob in blob['osxcollector_vtdomain']:
            for key in ['domain', 'detections']:
                val = blob.get(key)
                self._summarize_val(key, val, 'vtdomain')

    def _summarize_opendns(self, blob):
        for blob in blob['osxcollector_opendns']:
            for key in ['domain', 'categorization', 'security', 'link']:
                val = blob.get(key)
                self._summarize_val(key, val, 'opendns')

    def _summarize_val(self, key, val, prefix=None):
        self._print_key(key, prefix)
        self._print_val(val)
        self._write('\n')

    def _print_key(self, key, prefix):
        if not prefix:
            prefix = ''
        else:
            prefix += '-'

        self._write('  {0}{1}'.format(prefix, key), self.KEY_COLOR)
        self._write(': ')

    def _print_val(self, val):
        if isinstance(val, list):
            self._write('[')
            for index, elem in enumerate(val):
                self._print_val(elem)
                if index != len(val) - 1:
                    self._write(', ')
            self._write(']')
        elif isinstance(val, dict):
            self._write('{')
            keys = val.keys()
            for index, key in enumerate(keys):
                self._write('"')
                self._write(key, self.VAL_COLOR)
                self._write('": ')
                self._print_val(val[key])
                if index != len(keys) - 1:
                    self._write(', ')
            self._write('}')
        elif isinstance(val, basestring):
            val = val[:480]
            self._write('"')
            self._write(val, self.VAL_COLOR)
            self._write('"')
        elif isinstance(val, Number):
            self._write('{0}'.format(val), self.VAL_COLOR)


class _HtmlSummaryFilter(OutputFilter):

    def __init__(self, monochrome=False, show_signature_chain=False, show_browser_ext=False, **kwargs):
        super(_HtmlSummaryFilter, self).__init__(**kwargs)
        self._vthash = []
        self._vtdomain = []
        self._opendns = []
        self._blacklist = []
        self._related = []
        self._signature_chain = []
        self._extensions = []
        self._monochrome = monochrome
        self._show_signature_chain = show_signature_chain
        self._show_browser_ext = show_browser_ext

        self._add_to_blacklist = []

    def filter_line(self, blob):
        """Each Line of OSXCollector output will be passed to filter_line.

        The OutputFilter should return the line, either modified or unmodified.
        The OutputFilter can also choose to return nothing, effectively swallowing the line.

        Args:
            output_line: A dict

        Returns:
            A dict or None
        """
        if 'osxcollector_vthash' in blob:
            self._vthash.append(blob)

        if 'osxcollector_vtdomain' in blob:
            self._vtdomain.append(blob)

        if 'osxcollector_opendns' in blob:
            self._opendns.append(blob)

        if 'osxcollector_blacklist' in blob:
            self._blacklist.append(blob)

        if 'osxcollector_related' in blob:
            self._related.append(blob)

        if self._show_signature_chain:
            if 'signature_chain' in blob and blob['osxcollector_section'] in ['startup', 'kext']:
                signature_chain = blob['signature_chain']
                if not len(signature_chain) or 'Apple Root CA' != signature_chain[-1]:
                    self._signature_chain.append(blob)

        if self._show_browser_ext:
            if blob['osxcollector_section'] in ['firefox', 'chrome'] and blob.get('osxcollector_subsection') == 'extensions':
                self._extensions.append(blob)

        return blob

    def _write(self, text):
        try:
            sys.stdout.write(text.encode('utf-8', errors='ignore'))
        except UnicodeDecodeError as err:
            sys.stdout.write(text)
            sys.stderr.write('Unicode decode error: {0}'.format(err))

    def end_of_lines(self):
        """Called after all lines have been fed to filter_output_line.

        The OutputFilter can do any batch processing on that requires the complete input.

        Returns:
            An array of dicts (empty array if no lines remain)
        """
        self._write('''<html><head><style>
            body {
                color: #ffffff;
                background-color: #36454F;
            }

            p, h1, h2 {
                color: #ffff00;
                font-weight: bold;
            }

            dt {
                color: #a020f0;
            }

            .dd {
                color: #00ff00;
            }
        </style></head><body>''')
        self._print_header('Very Readable Output Bot')
        self._print_para('Let\'s see what\'s up with this machine.')

        if len(self._vthash):
            # <div id="vthash">
            self._print_header('VirusTotal bad hash hits', level=2)
            self._print_para('Dang! You\'ve got known malware on this machine. Hope it\'s commodity stuff')
            self._summarize_blobs(self._vthash)
            self._print_para('Sheesh! This is why we can\'t have nice things!')

        if len(self._vtdomain):
            # <div id="vtdomain">
            self._print_header('VirusTotal bad domain hits', level=2)
            self._print_para('I see you\'ve been visiting some \'questionable\' sites. If you trust VirusTotal that is.')
            self._summarize_blobs(self._vtdomain)
            self._print_para('I hope it was worth it!')

        if len(self._opendns):
            # <div id="opendns">
            self._print_header('OpenDNS Investigate hits', level=2)
            self._print_para('Well, here\'s some domains OpenDNS wouldn\'t recommend.')
            self._summarize_blobs(self._opendns)
            self._print_para('You know you shouldn\'t just click every link you see? #truth')

        if len(self._blacklist):
            # <div id="blacklist">
            self._print_header('Blacklist hits', level=2)
            self._print_para('We put stuff on a blacklist for a reason. Mostly so you don\'t do this.')
            self._summarize_blobs(self._blacklist)
            self._print_para('SMH')

        if len(self._related):
            # <div id="related">
            self._print_header('Related hits', level=2)
            self._print_para('This whole things started with just a few clues. Now look what I found.')
            self._summarize_blobs(self._related)
            self._print_para('Nothing hides from Very Readable Output Bot')

        if len(self._signature_chain):
            # <div id="signature_chain">
            self._print_header('Signature chain', level=2)
            self._print_para('If these binaries were signed by \'Apple Root CA\' I\'d trust them more.')
            self._summarize_blobs(self._signature_chain)
            self._print_para('Let\'s just try and stick with some safe software')

        if len(self._extensions):
            # <div id="extensions">
            self._print_header('Extensions', level=2)
            self._print_para('Let\'s see what\'s hiding in the browser, shall we.')
            self._summarize_blobs(self._extensions)
            self._print_para('You know these things have privileges galore.')

        if len(self._add_to_blacklist):
            # <div id="add_to_blacklist">
            self._add_to_blacklist = list(set(self._add_to_blacklist))
            self._print_header('Blacklist update suggestions', level=2)
            self._print_para('If I were you, I\'d probably update my blacklists to include:')
            for key, val in self._add_to_blacklist:
                self._summarize_val(key, val)
            self._print_para('That might just help things, Skippy!')

        self._print_para('Very Readable Output Bot')
        self._print_para('#kaythanksbye')

        self._write('</body></html>')

        return []

    def _summarize_blobs(self, blobs):
        for blob in blobs:
            self._summarize_line(blob)

            add_to_blacklist = False

            if 'osxcollector_vthash' in blob:
                self._summarize_vthash(blob)
                add_to_blacklist = True

            if 'osxcollector_vtdomain' in blob:
                self._summarize_vtdomain(blob)

            if 'osxcollector_opendns' in blob:
                self._summarize_opendns(blob)

            if 'osxcollector_blacklist' in blob:
                for key in blob['osxcollector_blacklist'].keys():
                    self._summarize_val('blacklist-{0}'.format(key), blob['osxcollector_blacklist'][key])

            if 'osxcollector_related' in blob:
                for key in blob['osxcollector_related'].keys():
                    self._summarize_val('related-{0}'.format(key), blob['osxcollector_related'][key])

            if 'md5' in blob and '' == blob['md5']:
                add_to_blacklist = True

            if add_to_blacklist:
                blacklists = blob.get('osxcollector_blacklist', {})
                values_on_blacklist = blacklists.get('hashes', [])
                for key in ['md5', 'sha1', 'sha2']:
                    val = blob.get(key, '')
                    if len(val) and val not in values_on_blacklist:
                        self._add_to_blacklist.append((key, val))

                values_on_blacklist = blacklists.get('domains', [])
                for domain in blob.get('osxcollector_domains', []):
                    if domain not in values_on_blacklist:
                        self._add_to_blacklist.append(('domain', domain))

    def _summarize_line(self, blob):
        section = blob.get('osxcollector_section')
        subsection = blob.get('osxcollector_subsection', '')

        self._print_header('{0} {1}'.format(section, subsection), level=3)
        for key in sorted(blob.keys()):
            if not key.startswith('osxcollector') and blob.get(key):
                val = blob.get(key)
                self._summarize_val(key, val)

    def _summarize_vthash(self, blob):
        for blob in blob['osxcollector_vthash']:
            for key in ['positives', 'total', 'scan_date', 'permalink']:
                val = blob.get(key)
                self._summarize_val(key, val, 'vthash')

    def _summarize_vtdomain(self, blob):
        for blob in blob['osxcollector_vtdomain']:
            for key in ['domain', 'detections']:
                val = blob.get(key)
                self._summarize_val(key, val, 'vtdomain')

    def _summarize_opendns(self, blob):
        for blob in blob['osxcollector_opendns']:
            for key in ['domain', 'categorization', 'security', 'link']:
                val = blob.get(key)
                self._summarize_val(key, val, 'opendns')

    def _summarize_val(self, key, val, prefix=None):
        self._print_key(key, prefix)
        self._print_val(val)

    def _print_header(self, text, level=1):
        self._write('<h{0}>{1}</h{0}>'.format(level, text))

    def _print_para(self, text):
        self._write('<p>{0}</p>'.format(text))

    def _print_list_item(self, item):
        self._write('<li>')
        self._print_val(item)
        self._write('</li>')

    def _print_key(self, key, prefix):
        if not prefix:
            prefix = ''
        else:
            prefix += '-'

        self._write('<dt>{0}{1}</dt>'.format(prefix, key))

    def _print_val(self, val):
        if isinstance(val, list):
            self._write('<ul>')
            for v in val:
                self._print_list_item(v)
            self._write('</ul>')
        elif isinstance(val, dict):
            self._write('<dl>')
            keys = val.keys()
            for key in keys:
                self._write('<dt>{0}</dt>'.format(key))
                self._print_val(val[key])
            self._write('</dl>')
        elif isinstance(val, basestring) or isinstance(val, Number):
            self._write('<dd>{0}</dd>'.format(val))


def main():
    run_filter_main(AnalyzeFilter)


if __name__ == "__main__":
    main()

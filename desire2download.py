#!/usr/bin/env python
# encoding: utf-8
"""
desire2download.py

Copyright 2012 Stephen Holiday

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

   http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import re
import os
import urllib2
import mechanize
import BeautifulSoup
import shutil

import sys

reload(sys)
sys.setdefaultencoding("utf-8")


class AuthError(Exception):
    """Raised when login credentials fail."""
    pass


class Desire2Download(object):
    base_url = 'https://learn.uwaterloo.ca/d2l/lp/homepage/home.d2l?ou=6606'
    cas_login = 'https://cas.uwaterloo.ca/cas/login?service=http%3a%2f%2flearn.uwaterloo.ca%2fd2l%2forgtools%2fCAS%2fDefault.aspx'
    ping_url = 'http://jobminestats.appspot.com/Ping/ag5zfmpvYm1pbmVzdGF0c3IMCxIFUGl4ZWwYuRcM.gif'

    def __init__(self, username, password, ignore_re=None, retries=3, skip_existing=True):
        self.username = username
        self.password = password
        self.ignore_re = ignore_re
        self.retries = retries
        self.skip_existing = skip_existing

        self.br = mechanize.Browser(factory=mechanize.RobustFactory())
        self.br.addheaders = [('User-agent', 'Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.0.1) \
                                    Gecko/2008071615 Fedora/3.0.1-1.fc9 Firefox/3.0.1')]
        self.br.set_handle_refresh(mechanize._http.HTTPRefreshProcessor(), max_time=1)

        self.br.open(self.ping_url).read()

    def retry(f):
        """Decorator to retry upon timeout. D2L is slow."""

        def retry_it(self, *args, **kargs):
            attempts = 0
            while attempts < self.retries:
                try:
                    return f(self, *args, **kargs)
                except urllib2.URLError as e:
                    if isinstance(e.reason, socket.timeout):
                        attempts += 1
                        if attempts >= self.retries:
                            print "Timeout, out of retries."
                            raise (e)
                        print "Timeout, retrying..."
                    else:
                    # Not a timeout, raise exception
                        print "Unknown exception:", e
                        raise (e)

        return retry_it

    @retry
    def login(self):
        print 'Logging In...'
        self.br.open(self.cas_login)
        self.br.select_form(nr=0)
        self.br['username'] = self.username
        self.br['password'] = self.password
        response = self.br.submit().read()
        if "Your userid and/or your password are incorrect" in response:
            raise AuthError("Your userid and/or your password are incorrect.")
        print 'Logged In'

    def get_course_links(self):
        print 'Finding courses...'
        links = []
        urls = []
        for link in self.br.links():
            link.text = link.text if link.text else ""
            matches = re.match('[A-Z]+ [0-9A-Za-z/\s]{2,45} - [A-Z][a-z]+ 20[0-9]{2}', link.text)
            if matches is not None and link.url not in urls:
                links.append(link)
                urls.append(link.url)
        return links

    def convert_bytes(self, bytes):
        """
            Stolen from http://www.5dollarwhitebox.org/drupal/node/84
        """
        bytes = float(bytes)
        if bytes >= 1099511627776:
            terabytes = bytes / 1099511627776
            size = '%.2fT' % terabytes
        elif bytes >= 1073741824:
            gigabytes = bytes / 1073741824
            size = '%.2fG' % gigabytes
        elif bytes >= 1048576:
            megabytes = bytes / 1048576
            size = '%.2fM' % megabytes
        elif bytes >= 1024:
            kilobytes = bytes / 1024
            size = '%.2fK' % kilobytes
        else:
            size = '%.2fb' % bytes
        return size

    @retry
    def get_course_documents(self, link, course_name):
        """Produce a tree of documents for the course.

        Args:
            link (str): A url to the course's page on d2l.
            course_name (str): The name of the course.

        Returns:
            A dict representing a tree:
            {
                'type': Either 'file' or 'dir',
                'name': A string.
                'url': Url to the file preview (if file).
                'children': A list of children nodes (if a dir).
            }
        """
        self.br.follow_link(link)                    # Go to course page
        link = self.br.links(text='Content').next()  # Get content link
        page = self.br.follow_link(link).read()      # Go to content page
        soup = BeautifulSoup.BeautifulSoup(page)
        contents = soup.find('ul', 'd2l-datalist')

        ## Initial document tree
        document_tree = {
            'type': 'dir',
            'name': course_name,
            'children': []
        }
        ## Keeps track of current location in tree
        path_to_root = [document_tree]

        sections = contents.findAll('li', 'd2l-datalist-item')
        for section in sections:
            ## Update path_to_root
            if section.find('h2') is None:
                continue
            heading = section.find('h2').getText()

            section_node = {
                'type': 'dir',
                'name': "".join(x for x in heading if x.isalnum()),
                'children': []
            }
            path_to_root[-1]['children'].append(section_node)
            path_to_section = path_to_root[-1]['children'][-1]

            ## Generate new node, whether a file or dir, and append it
            ## to the children of the current level (last in path_to_root)
            for d2l_link in section.findAll('a', 'd2l-link'):
                section_number = re.search('/content/([0-9]+)', d2l_link['href']).group(1)
                content_number = re.search('/viewContent/([0-9]+)', d2l_link['href']).group(1)
                link_href = 'https://learn.uwaterloo.ca/d2l/le/content/%s/topics/files/download/%s/DirectFileTopicDownload' % (
                    section_number, content_number)
                node = {
                    'type': 'file',
                    'name': d2l_link.getText(),
                    'url': link_href,
                }
                path_to_section['children'].append(node)

            for d2l_click in section.findAll('a', 'd2l-clickable'):
                print(d2l_click)

        return document_tree

    def download_tree(self, root, _path=[]):
        """Downloads the entire file tree the

        Args:
            root: A dictionary containing the file tree.
            _path: A list representing the path (relative to current dir) to
                download to. Items in list are strings.
        """
        if root['type'] == 'dir':
            path = _path[:]
            path.append(root['name'])
            for node in root['children']:
                self.download_tree(node, path)
        else:
            path = '/'.join(map(lambda x: x.replace('/', '-'), _path))
            self.download_file(root['name'], root['url'], path)

    def download_file(self, title, url, path):
        """Downloads a file to the specified directory.

        Args:
            title (str): Name of the file.
            url (str): Address to the direct link.
            path (str): Relative path of file to make.
        """
        try:
            os.makedirs(path)
        except OSError as e:
            if e.errno != 17:
                raise e
            pass

        for r in self.ignore_re:
            if r.match(title) is not None:
                print 'Skipping %s because it matches ignore regex "%s"' % (title, r.pattern)
                return

        path_and_filename = '%s/%s' % (path, title.strip('/'))
        if os.path.isdir(path_and_filename): # Handle empty file names
            print ' X %s is a directory, not a file. Skipping.' % path_and_filename
        elif os.path.isfile(path_and_filename) and self.skip_existing:  # TODO Can we make this smarter?
            print ' - %s (Already Saved)' % path_and_filename
        else:
            try:
                print ' + %s' % path_and_filename
                (fn, headers) = self.br.retrieve(url, path_and_filename, self._progressBar)
                extension = headers.subtype
                shutil.move(path_and_filename, path_and_filename + '.' + extension) # TODO: This sucks
            except KeyboardInterrupt:
                # delete the file on a keyboard interrupt
                if os.path.exists(path_and_filename):
                    os.remove(path_and_filename)
                raise
            except urllib2.HTTPError, e:
                if e.code == 404:
                    print " X File does not exist: %s" % title.strip('/')
                else:
                    print " X HTTP error %s for: %s" % (e.code, title.strip('/'))
            except Exception, e:
                # otherwise raise the error
                if os.path.exists(path_and_filename):
                    os.remove(path_and_filename)
                else:
                    raise

    def _progressBar(self, blocknum, bs, size):
        """
            Stolen from https://github.com/KartikTalwar/Coursera/blob/master/coursera.py
        """
        if size > 0:
            if size % bs != 0:
                blockCount = size/bs + 1
            else:
                blockCount = size/bs

            fraction = blocknum*1.0/blockCount
            width    = 50

            stars    = '*' * int(width * fraction)
            spaces   = ' ' * (width - len(stars))
            progress = ' ' * 3 + '%s [%s%s] (%s%%)' % (self.convert_bytes(size), stars, spaces, int(fraction * 100))

            if fraction*100 < 100:
                sys.stdout.write(progress)

                if blocknum < blockCount:
                    sys.stdout.write('\r')
                else:
                    sys.stdout.write('\n')
            else:
                sys.stdout.write(' ' * int(width * 1.5) + '\r')
                sys.stdout.flush()

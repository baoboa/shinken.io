#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (C) 2012:
#    Gabes Jean, naparuba@gmail.com


import os
import sys
import time
import pymongo
import hashlib
import uuid
import ConfigParser
import tarfile
import json
import shutil
from pprint import pprint
import markdown
from twitter import Twitter, OAuth

class Groker():
    def __init__(self, cfg_path):
        self.conf = ConfigParser.SafeConfigParser()
        self.conf.read(cfg_path)
        self.data = self.conf.get('http', 'data')
        self.data_in = self.data+'/in'
        self.data_tmp = self.data+'/tmp'
        #self.data_packages = self.data+'/packages'
        self.data_users = self.data+'/users'
        self.open_database()

    
    
    def open_database(self):
        self.con = pymongo.Connection('localhost')
        print "Shinken.IO Connected to:", self.con
        self.db    = self.con.shinken_io 
        self.users = self.db.users
        self.packages = self.db.packages
        self.modules = self.db.modules


    def run(self):
        print "Starting to grok directory", self.data_in
        for (dirpath, dirnames, filenames) in os.walk(self.data_in):
            print (dirpath, dirnames, filenames)
            user = os.path.split(dirpath)[1]
            for filename in filenames:
                if not filename.endswith('.tar.gz'):
                    continue
                f = os.path.join(dirpath, filename)
                print "Will look at the file", f
                try:
                    tar = tarfile.open(f, 'r:gz')
                except tarfile.TarError, exp:
                    print "ERROR processing the file", f, ":", exp
                    continue

                package_json = ''
                # First look at strange file inside, like .. or / things
                # Or also too big files inside
                invalid_files = too_big = False
                total_size = 0
                try:
                    members = tar.getmembers()
                except tarfile.TarError, exp:
                    print "ERROR processing the file", f, ":", exp
                    continue
                for m in members:
                    print m.name
                    if os.path.split(m.name)[1] == 'package.json':
                        package_json = m.name
                    if m.name.startswith('/') or '..' in m.name:
                        invalid_files = True
                    # Should be a dir or a file. nothing else!
                    if not m.isfile() and not m.isdir():
                        invalid_files = True
                    total_size += m.size
                    if m.size > 5000000:
                        too_big = True
                if invalid_files:
                    print "SECURITY: The archive %s contains / or .. files, skiping it" % f
                    continue
                if too_big:
                    print "SECURITY: The archive %s contains too big files, skipping it" % f
                    continue
                if total_size > 20000000:
                    print "SECURITY: The archive %s is just too big! %db" % (f, total_size)
                    continue
                if not package_json:
                    print "INVALID: The archive %s do not contains package.json file" % f
                    continue

                print "INFO: The archive %s seems valid" % f
                try:
                    fd = tar.extractfile(package_json)
                    buf = fd.read()
                    fd.close()
                except tarfile.TarError, exp:
                    print "ERROR processing the file", f, ":", exp
                    continue

                print "Looking for README.md file in %s" % f
                readme = ''
                try:
                    readme_fd = tar.extractfile('./README.md')
                    readme = readme_fd.read().decode('utf8', 'ignore')
                    readme_fd.close()
                except (tarfile.TarError,KeyError) , exp:
                    # Maybe the file is missing, not a problem
                    pass


                pack = self.parse_package_json(buf)
                # Ok maybe it's an invalide one?
                if not pack.get('name'):
                    print "The pack got no name entry, skipping it"
                    continue

                # Dest file will be in data/
                self.create_or_update_pack(user, pack, f, readme)


    def assume_string(self, s):
        if not isinstance(s, basestring):
            return ''
        try:
            return s.encode('utf8', 'ignore')
        except:
            return ''


    def assume_list_of_strings(self, l):
        r = []
        if not isinstance(l, list):
            return []
        for s in l:
            r.append(self.assume_string(s))
        return r


    def clean_name(self, name):
        ok_chars = 'azertyuiopqsdfghjklmwxcvbn-_1234567890'
        return ''.join([c for c in name if c in ok_chars])
        
    

    def parse_package_json(self, buf):
        pack = {'name':'', 'types':[], 'version':'0', 'homepage':'',
                'keywords':[], 'updated':int(time.time())}
        p = json.loads(buf)

        name = self.assume_string(p.get('name', ''))
        name = self.clean_name(name)
        pack['name'] = name
        pack['types'] = self.assume_list_of_strings(p.get('types', []))
        pack['keywords'] = self.assume_list_of_strings(p.get('keywords', []))
        pack['version'] = self.assume_string(p.get('version', '0'))
        pack['homepage'] = self.assume_string(p.get('homepage', ''))
        pack['description'] = self.assume_string(p.get('description', ''))
        pack['repository'] = self.assume_string(p.get('repository', ''))
        
        
        return pack

        
    def get_user(self, user_name):
        return self.users.find_one({'_id' : user_name})


    def get_pack(self, pname):
        return self.packages.find_one({'_id' : pname})


    def post_twitter_new_package(self, pname):        
        access_token = self.conf.get('twitter', 'access_token')
        access_secret = self.conf.get('twitter', 'access_secret')
        consumer_key = self.conf.get('twitter', 'consumer_key')
        consumer_secret = self.conf.get('twitter', 'consumer_secret')
        
        try:
            t = Twitter(auth=OAuth(access_token,access_secret,consumer_key,consumer_secret))
            t.statuses.update(status="New package available: %s  http://shinken.io/package/%s" % (pname,pname))
        except Exception, exp:
            print "ERROR IN TWITTER POST", exp



    def create_or_update_pack(self, user, pack, archive_in, readme):
        pname = pack.get('name')
        prev  = self.get_pack(pname)
        if prev:
            print "THERE IS A PREVIOUS PACK", prev
            print "It was on the user", prev['user_id']
            if user != prev['user_id']:
                print "SECURITY: The user %s try to push the pack %s pf the user %s" % (user, pack['name'], prev['user_id'])
                return
            # Ok update the prev entry
            prev.update(pack)
            print "WILL SAVE for update"*100, prev
            self.packages.update({'_id':pname}, prev)
        else:
            p_entry = {
                "_id"          : pname,
                "user_id"      : user,
                "creation_time": int(time.time()),
                "dependencies"   : [],
                "dependents"     : [],
                "starred"        : [],
                "starred_len"    : 0,
                }
            p_entry.update(pack)
            print "WILL SAVE FOR INSERT", p_entry
            self.packages.insert(p_entry)

            # Increase the number of packages for this user
            user_entry = self.get_user(user)
            nb = user_entry.get('nb_packages', 0)
            user_entry['nb_packages'] = nb + 1
            print "NOW THE USER %s GOT %d packages" % (user, nb + 1)
            self.users.update({'_id': user}, user_entry)
            
            # Also send a twitter news
            self.post_twitter_new_package(pname)
            
        # Now move the file in the good place
        # If not exists, the directory should be readalbe by every one
        user_dir = os.path.join(self.data_users, user)
        if not os.path.exists(user_dir):
            os.mkdir(user_dir)
            os.chmod(user_dir, 0o755)
        
        packages_dir = os.path.join(user_dir, 'packages')
        if not os.path.exists(packages_dir):
            os.mkdir(packages_dir)
            os.chmod(packages_dir, 0o755)

        dest_dir = os.path.join(packages_dir, pname)
        if not os.path.exists(dest_dir):
            os.mkdir(dest_dir)
            os.chmod(dest_dir, 0o755)
        
        dest_file = os.path.join(dest_dir, pname+'.tar.gz')
        print "DEST", archive_in, dest_file
        try:
            shutil.move(archive_in, dest_file)
        except OSError, exp:
            print "ERROR: cannot move archive file from %s to %s : %s" % (archive_in, dest_file, str(exp))
            return

        # Now save the readme.md and make a readme.html version too
        if readme:
            readme_path = os.path.join(dest_dir, 'README.md')
            fd = open(readme_path+'tmp', 'w')
            fd.write(readme)
            fd.close()
            shutil.move(readme_path+'tmp', readme_path)
            print "SAVED README.md file at %s" % readme_path
            # Now parse it and transform it in HTML
            html = markdown.markdown(readme, extensions=['toc', 'nl2br',
                                                          'codehilite']
                                     )
            html_path = os.path.join(dest_dir, 'README.html')
            fd = open(html_path+'tmp', 'w')
            fd.write(html)
            fd.close()
            shutil.move(html_path+'tmp', html_path)
            print "SAVED README.html file at %s" % readme_path


        print "UPDATE: the archive %s is updated" % dest_file
        


if __name__ == '__main__':
    g = Groker('/opt/shinken.io/config.ini')
    g.run()

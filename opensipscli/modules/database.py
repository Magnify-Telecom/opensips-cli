#!/usr/bin/env python
##
## This file is part of OpenSIPS CLI
## (see https://github.com/OpenSIPS/opensips-cli).
##
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program. If not, see <http://www.gnu.org/licenses/>.
##

from opensipscli.module import Module
from opensipscli.logger import logger
from opensipscli.config import cfg
from opensipscli.db import (
    osdb, osdbError, osdbConnectError,
    osdbArgumentError, osdbNoSuchModuleError,
    osdbModuleAlreadyExistsError, osdbAccessDeniedError,
    SUPPORTED_BACKENDS,
)

import os, re
from getpass import getpass, getuser

DEFAULT_DB_TEMPLATE = "template1"

STANDARD_DB_MODULES = [
    "acc",
    "alias_db",
    "auth_db",
    "avpops",
    "clusterer",
    "dialog",
    "dialplan",
    "dispatcher",
    "domain",
    "drouting",
    "group",
    "load_balancer",
    "msilo",
    "permissions",
    "rtpproxy",
    "rtpengine",
    "speeddial",
    "tls_mgm",
    "usrloc"
]

EXTRA_DB_MODULES = [
    "b2b",
    "b2b_sca",
    "call_center",
    "carrierroute",
    "closeddial",
    "domainpolicy",
    "emergency",
    "fraud_detection",
    "freeswitch_scripting",
    "imc",
    "load_balancer",
    "presence",
    "registrant",
    "rls",
    "smpp",
    "tracer",
    "userblacklist"
]

MIGRATE_TABLES_24_TO_30 = [
    'registrant', # changed in 3.0
    'tls_mgm',    # changed in 3.0
    'acc',
    'address',
    'cachedb',
    'carrierfailureroute',
    'carrierroute',
    'cc_agents',
    'cc_calls',
    'cc_cdrs',
    'cc_flows',
    'closeddial',
    'clusterer',
    'cpl',
    'dbaliases',
    'dialplan',
    'dispatcher',
    'domain',
    'domainpolicy',
    'dr_carriers',
    'dr_gateways',
    'dr_groups',
    'dr_partitions',
    'dr_rules',
    'emergency_report',
    'emergency_routing',
    'emergency_service_provider',
    'fraud_detection',
    'freeswitch',
    'globalblacklist',
    'grp',
    'imc_members',
    'imc_rooms',
    'load_balancer',
    'location',
    'missed_calls',
    'presentity',
    'pua',
    're_grp',
    'rls_presentity',
    'rls_watchers',
    'route_tree',
    'rtpengine',
    'rtpproxy_sockets',
    'silo',
    'sip_trace',
    'smpp',
    'speed_dial',
    'subscriber',
    'uri',
    'userblacklist',
    'usr_preferences',
    'xcap',
    ]


class database(Module):
    """
    Class: database modules
    """
    def __init__(self, *args, **kwargs):
        """
        Constructor
        """
        super().__init__(*args, **kwargs)
        self.db_path = None

    def __complete__(self, command, text, line, begidx, endidx):
        """
        helper for autocompletion in interactive mode
        """

        if command == 'create':
            db_name = ['opensips', 'opensips_test']
            if not text:
                return db_name
            ret = [t for t in db_name if t.startswith(text)]
        elif command == 'add':
            modules = STANDARD_DB_MODULES + EXTRA_DB_MODULES
            if not text:
                return modules

            ret = [t for t in modules if t.startswith(text)]
        elif command == 'migrate':
            db_source = ['opensips']
            if not text:
                return db_source
            ret = [t for t in db_source if t.startswith(text)]

            db_dest = ['opensips_new']
            if not text:
                return db_dest
            ret = [t for t in db_dest if t.startswith(text)]

        return ret or ['']

    def __exclude__(self):
        """
        method exlusion list
        """
        if cfg.exists("database_url"):
            db_url = cfg.get("database_url")
            return not osdb.has_dialect(osdb.get_dialect(db_url))
        else:
            return not osdb.has_sqlalchemy()

    def __get_methods__(self):
        """
        methods available for autocompletion
        """
        return [
            'create',
            'drop',
            'add',
            'migrate',
            ]

    def get_db_url(self, db_name=cfg.get('database_name')):
        engine = osdb.get_db_engine()
        if not engine:
            return None

        # make sure to inherit the 'database_admin_url' engine
        db_url = osdb.set_url_driver(cfg.get("database_url"), engine)

        logger.debug("DB URL: '{}'".format(db_url))
        return db_url

    def get_admin_db_url(self, db_name):
        engine = osdb.get_db_engine()
        if not engine:
            return None

        if cfg.exists('database_admin_url'):
            admin_url = cfg.get("database_admin_url")
            if engine == "postgres":
                admin_url = osdb.set_url_db(admin_url, 'postgres')
        else:
            if engine == 'postgres':
                if getuser() != "postgres":
                    logger.error("Command must be run as 'postgres' user: "
                                 "sudo -u postgres opensips-cli ...")
                    return None

                """
                For PG, do the initial setup using 'postgres' as role + DB
                """
                admin_url = "postgres://postgres@localhost/postgres"
            else:
                admin_url = "{}://root@localhost".format(engine)

        if osdb.get_url_pswd(admin_url) is None:
            pswd = getpass("Password for admin DB user ({}): ".format(
                            osdb.get_url_user(admin_url)))
            logger.debug("read password: '%s'", pswd)
            admin_url = osdb.set_url_password(admin_url, pswd)

        logger.debug("admin DB URL: '{}'".format(admin_url))
        return admin_url

    def do_add(self, params):
        """
        add a given table to the database (connection via URL)
        """
        if len(params) < 1:
            logger.error("Please specify a module to add (e.g. dialog)")
            return -1
        module = params[0]

        if len(params) < 2:
            db_name = cfg.read_param("database_name",
                    "Please provide the database to add the module to")
        else:
            db_name = params[1]

        db_url = self.get_db_url(db_name)
        if not db_url:
            logger.error("no DB URL specified: aborting!")
            return -1

        admin_url = self.get_admin_db_url(db_name)
        if not admin_url:
            return -1

        admin_db = self.get_db(admin_url, db_name)
        if not admin_db:
            return -1

        ret = self.create_tables(db_name, db_url, admin_db, tables=[module],
                                 create_std=False)

        admin_db.destroy()
        return ret


    def do_create(self, params=None):
        """
        create database with role-assigment and tables
        """
        if len(params) >= 1:
            db_name = params[0]
        else:
            db_name = cfg.read_param("database_name",
                "Please provide the database to create")
        logger.debug("db_name: '%s'", db_name)

        admin_url = self.get_admin_db_url(db_name)
        if not admin_url:
            return -1

        admin_db = self.get_db(admin_url, db_name)
        if not admin_db:
            return -1

        if self.create_db(db_name, admin_url, admin_db) < 0:
            return -1

        db_url = self.get_db_url(db_name)
        if not db_url:
            return -1

        if self.ensure_user(db_url, db_name, admin_db) < 0:
            return -1

        if self.create_tables(db_name, db_url, admin_db) < 0:
            return -1

        admin_db.destroy()
        return 0

    def create_db(self, db_name, admin_url, db=None):
        # 1) create an object store database instance
        #    -> use it to create the database itself
        if not db:
            db = self.get_db(admin_url, db_name)
            if not db:
                return -1
            destroy = True
        else:
            destroy = False

        # check to see if the database has already been created
        if db.exists(db_name):
            logger.warn("database '%s' already exists!", db_name)
            return -2

        # create the db instance
        if not db.create(db_name):
            return -1

        if destroy:
            db.destroy()
        return 0

    def create_tables(self, db_name, db_url, admin_db, tables=[],
                        create_std=True):
        """
        create database tables
        """
        db_url = osdb.set_url_db(db_url, db_name)

        # 2) prepare new object store database instance
        #    use it to connect to the created database
        db = self.get_db(db_url, db_name)
        if db is None:
            return -1

        if not db.exists():
            logger.warning("database '{}' does not exist!".format(db_name))
            return -1

        schema_path = self.get_schema_path(db.dialect)
        if schema_path is None:
            return -1

        if create_std:
            standard_file_path = os.path.join(schema_path, "standard-create.sql")
            if not os.path.isfile(standard_file_path):
                logger.error("cannot find stardard OpenSIPS DB file: '{}'!".
                        format(standard_file_path))
                return -1
            table_files = {'standard': standard_file_path}
        else:
            table_files = {}

        # check to see what tables we shall deploy
        if tables:
            pass
        elif cfg.exists("database_modules"):
            # we know exactly what modules we want to instsall
            tables_line = cfg.get("database_modules").strip().lower()
            if tables_line == "all":
                logger.debug("Creating all tables")
                tables = [ f.replace('-create.sql', '') \
                            for f in os.listdir(schema_path) \
                            if os.path.isfile(os.path.join(schema_path, f)) and \
                                f.endswith('-create.sql') ]
            else:
                logger.debug("Creating custom tables")
                tables = tables_line.split(" ")
        else:
            logger.debug("Creating standard tables")
            tables = STANDARD_DB_MODULES

        # check for corresponding SQL schemas files in system path
        logger.debug("checking tables: {}".format(" ".join(tables)))

        for table in tables:
            if table == "standard":
                # already checked for it
                continue
            table_file_path = os.path.join(schema_path,
                    "{}-create.sql".format(table))
            if not os.path.isfile(table_file_path):
                logger.warn("cannot find SQL file for module {}: {}".
                        format(table, table_file_path))
            else:
                table_files[table] = table_file_path

        username = osdb.get_url_user(db_url)
        admin_db.connect(db_name)

        # create tables from SQL schemas
        for module, table_file in table_files.items():
            logger.info("Running {}...".format(os.path.basename(table_file)))
            try:
                db.create_module(table_file)
                if db.dialect == "postgres":
                    self.pg_grant_table_access(table_file, username, admin_db)
            except osdbModuleAlreadyExistsError:
                logger.error("{} table(s) are already created!".format(module))
            except osdbError as ex:
                logger.error("cannot import: {}".format(ex))

        # terminate active database connection
        db.destroy()
        return 0

    def ensure_user(self, db_url, db_name, admin_db):
        """
        Ensures that the user/password in @db_url can connect to @db_name.
        It assumes @db_name has been created beforehand.  If the user doesn't
        exist or has insufficient permissions, this will be fixed using the
        @admin_db connection.
        """
        db_url = osdb.set_url_db(db_url, db_name)

        try:
            db = self.get_db(db_url, db_name, check_access=True)
            logger.info("access works, opensips user already exists")
        except osdbAccessDeniedError:
            logger.info("creating access user for {} ...".format(db_name))
            if not admin_db.ensure_user(db_url):
                logger.error("failed to create user on {} DB".format(db_name))
                return -1

            try:
                db = self.get_db(db_url, db_name, check_access=True)
            except Exception as e:
                logger.exception(e)
                logger.error("failed to connect to {} " +
                                "with non-admin user".format(db_name))
                return -1

        db.destroy()
        return 0

    def do_drop(self, params=None):
        """
        drop a given database object (connection via URL)
        For PostgreSQL, perform this operation using 'postgres' as role + DB
        """
        if params and len(params) > 0:
            db_name = params[0]
        else:
            db_name = cfg.read_param("database_name",
                    "Please provide the database to drop")

        admin_db_url = self.get_admin_db_url(db_name)
        if admin_db_url is None:
            return -1

        if admin_db_url.lower().startswith("postgres"):
            admin_db_url = osdb.set_url_db(admin_db_url, 'postgres')

        # create an object store database instance
        db = self.get_db(admin_db_url, db_name)
        if db is None:
            return -1

        # check to see if the database has already been created
        if db.exists():
            if cfg.read_param("database_force_drop",
                "Do you really want to drop the '{}' database".
                    format(db_name),
                False, True, isbool=True):

                if db.drop():
                    logger.info("database '%s' dropped!", db_name)
                else:
                    logger.info("database '%s' not dropped!", db_name)
            else:
                logger.info("database '{}' not dropped!".format(db_name))
        else:
            logger.warning("database '{}' does not exist!".format(db_name))
            db.destroy()
            return -1

        db.destroy()
        return 0


    def do_migrate(self, params):
        if len(params) < 2:
            print("Usage: database migrate <old-database> <new-database>")
            return 0

        old_db = params[0]
        new_db = params[1]

        admin_url = self.get_admin_db_url(new_db)
        if not admin_url:
            return -1

        db = self.get_db(admin_url, new_db)
        if not db:
            return -1

        if db.dialect != "mysql":
            logger.error("'migrate' is only available for MySQL right now! :(")
            return -1

        if not db.exists(old_db):
             logger.error("the source database ({}) does not exist!".format(old_db))
             return -2

        print("Creating database {}...".format(new_db))
        if self.create_db(new_db, admin_url, db) < 0:
            return -1
        if self.create_tables(new_db, db, admin_url) < 0:
            return -1

        backend = osdb.get_url_driver(admin_url)

        # obtain the DB schema files for the in-use backend
        schema_path = self.get_schema_path(backend)
        if schema_path is None:
            return -1

        migrate_scripts = self.get_migrate_scripts_path(backend)
        if migrate_scripts is None:
            logger.debug("migration scripts for %s not found", backend)
            return -1
        else:
            logger.debug("migration scripts for %s", migrate_scripts)

        print("Migrating all matching OpenSIPS tables...")
        db.migrate(migrate_scripts, old_db, new_db, MIGRATE_TABLES_24_TO_30)

        print("Finished copying OpenSIPS table data " +
                "into database '{}'!".format(new_db))

        db.destroy()
        return True

    def get_db(self, db_url, db_name, cfg_url_param="database_admin_url",
                check_access=False):
        """
        helper function: check database url and its dialect
        """
        try:
            return osdb(db_url, db_name)
        except osdbAccessDeniedError:
            if check_access:
                raise
            logger.error("failed to connect to DB as %s, please provide or " +
                "fix the '%s'", osdb.get_url_user(db_url), cfg_url_param)
        except osdbArgumentError:
            logger.error("Bad URL, it should resemble: {}".format(
                "backend://user:pass@hostname" if not \
                    db_url.startswith('sqlite:') else "sqlite:///path/to/db"))
        except osdbConnectError:
            logger.error("Failed to connect to database!")
        except osdbNoSuchModuleError:
            logger.error("This database backend is not supported!  " \
                        "Supported: {}".format(', '.join(SUPPORTED_BACKENDS)))

    def get_migrate_scripts_path(self, backend):
        """
        helper function: migrate database schema
        """
        if '+' in backend:
            backend = backend[0:backend.index('+')]

        if self.db_path is not None:
            scripts = [
                os.path.join(self.db_path, backend, 'table-migrate.sql'),
                os.path.join(self.db_path, backend, 'db-migrate.sql'),
                ]

            if any(not os.path.isfile(i) for i in scripts):
                logger.error("The SQL migration scripts are missing!  " \
                            "Please pull the latest OpenSIPS packages!")
                return None

            return scripts

    def get_schema_path(self, backend):
        """
        helper function: get the path defining the root path holding sql schema template
        """
        if '+' in backend:
            backend = backend[0:backend.index('+')]

        if self.db_path is not None:
            return os.path.join(self.db_path, backend)

        if os.path.isfile(os.path.join('/usr/share/opensips',
                                backend, 'standard-create.sql')):
            self.db_path = '/usr/share/opensips'
            return os.path.join(self.db_path, backend)

        db_path = cfg.read_param("database_schema_path",
                "Could not locate DB schema files for {}!  Custom path".format(
                    backend))
        if db_path is None:
            print()
            logger.error("failed to locate {} DB schema files".format(backend))
            return None

        if db_path.endswith('/'):
            db_path = db_path[:-1]
        if os.path.basename(db_path) == backend:
            db_path = os.path.dirname(db_path)

        if not os.path.exists(db_path):
            logger.error("path '{}' to OpenSIPS DB scripts does not exist!".
                    format(db_path))
            return None
        if not os.path.isdir(db_path):
            logger.error("path '{}' to OpenSIPS DB scripts is not a directory!".
                    format(db_path))
            return None

        schema_path = os.path.join(db_path, backend)
        if not os.path.isdir(schema_path):
            logger.error("invalid OpenSIPS DB scripts dir: '{}'!".
                    format(schema_path))
            return None

        self.db_path = db_path
        return schema_path

    def pg_grant_table_access(self, sql_file, username, admin_db):
        """
        Grant access to all tables and sequence IDs of a DB module
        """
        with open(sql_file, "r") as f:
            for line in f.readlines():
                res = re.search('CREATE TABLE (.*) ', line, re.IGNORECASE)
                if res:
                    table = res.group(1)
                    admin_db.grant_table_options(username, table)

                res = re.search('ALTER SEQUENCE (.*) MAXVALUE', line,
                                re.IGNORECASE)
                if res:
                    seq = res.group(1)
                    admin_db.grant_table_options(username, seq)

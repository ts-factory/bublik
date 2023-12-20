#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import datetime

from sqlalchemy import Column, DateTime, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker


# ~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~ Constants
TESTS_NUM = 1000
ITERATIONS_NUM = 100
TEST_RUNS_NUM = 1000
CONFS_NUM = 1500
RESULTS_NUM = 50
TAGS_NUM = 50
TEST_RUN_TAGS_NUM = 30

# ~~~~~~~~~~~~~~~~~~~~~~~~~~~
# ~ Tables

Base = declarative_base()


class Tag(Base):
    __tablename__ = 'tag'
    tag_id = Column(Integer, primary_key=True)
    name = Column(String)
    value = Column(String)
    description = Column(String)

    def __init__(self, name, value, description):
        self.name = name
        self.value = value
        self.description = description

    def __repr__(self):
        return '<Tag (%d: %s, %s, %s)>' % (
            self.tag_id,
            self.name,
            self.value,
            self.description,
        )


class RunTag(Base):
    __tablename__ = 'run_tag'
    run_id = Column(Integer, primary_key=True)
    tag_id = Column(Integer, primary_key=True)

    def __init__(self, run_id, tag_id):
        self.run_id = run_id
        self.tag_id = tag_id

    def __repr__(self):
        return '<Run tag (%d, %d)>' % (self.run_id, self.tag_id)


class TestRun(Base):
    __tablename__ = 'test_run'
    test_run_id = Column(Integer, primary_key=True)
    date = Column(DateTime, default=datetime.datetime.utcnow)

    def __init__(self):
        self.config_id = self.test_run_id

    def __repr__(self):
        return '<Run (%d)>' % self.test_run_id


class Package(Base):
    __tablename__ = 'package'
    package_id = Column(Integer, primary_key=True)
    name = Column(String)
    parent_id = Column(Integer)

    def __init__(self, name, parent_id):
        self.name = name
        self.parent_id = parent_id

    def __repr__(self):
        return '<Package (%d: %s, %d)>' % (self.package_id, self.name, self.parent_id)


class Test(Base):
    __tablename__ = 'test'
    test_id = Column(Integer, primary_key=True)
    package_id = Column(Integer)
    name = Column(String)

    def __init__(self, package_id, name, argument, value):
        self.package_id = package_id
        self.name = name

    def __repr__(self):
        return '<Test (%d: %d, %s)>' % (self.test_id, self.package_id, self.name)


class TestIteration(Base):
    __tablename__ = 'test_iteration'
    test_iteration_id = Column(Integer, primary_key=True)
    test_id = Column(Integer)

    def __init__(self, test_id):
        self.test_id = test_id

    def __repr__(self):
        return '<Test iteration (%d: %d)>' % (self.test_iteration_id, self.test_id)


class Argument(Base):
    __tablename__ = 'argument'
    argument_id = Column(Integer, primary_key=True)
    name = Column(String)
    value = Column(String)

    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __repr__(self):
        return '<Argument (%d: %s, %s)>' % (self.argument_id, self.name, self.value)


class TestIterationArgument(Base):
    __tablename__ = 'test_iteration_argument'
    argument_id = Column(Integer, primary_key=True)
    test_iteration_id = Column(Integer, primary_key=True)

    def __init__(self, argument_id, test_iteration_id):
        self.argument_id = argument_id
        self.test_iteration_id = test_iteration_id

    def __repr__(self):
        return '<Test iteration argument (%d, %d)>' % (
            self.argument_id,
            self.test_iteration_id,
        )


class IterationResult(Base):
    __tablename__ = 'iteration_result'
    iteration_result_id = Column(Integer, primary_key=True)
    iteration_id = Column(Integer)
    run_id = Column(Integer)
    begin = Column(DateTime, default=datetime.datetime.utcnow)
    end = Column(DateTime, default=datetime.datetime.utcnow)

    def __init__(self, iteration_id, run_id):
        self.iteration_id = iteration_id
        self.run_id = run_id

    def __repr__(self):
        return '<Iteration result %d (%d: %d)>' % (
            self.iteration_result_id,
            self.iteration_id,
            self.run_id,
        )


class RunMeta(Base):
    __tablename__ = 'run_meta'
    run_id = Column(Integer, primary_key=True)
    meta_id = Column(Integer, primary_key=True)

    def __init__(self, run_id, meta_id):
        self.run_id = run_id
        self.meta_id = meta_id

    def __repr__(self):
        return '<Run meta (%d, %d)>' % (self.run_id, self.meta_id)


class IterationResultMeta(Base):
    __tablename__ = 'iteration_result_meta'
    iteration_result_id = Column(Integer, primary_key=True)
    meta_id = Column(Integer, primary_key=True)

    def __init__(self, iteration_result_id, meta_id):
        self.iteration_result_id = iteration_result_id
        self.meta_id = meta_id

    def __repr__(self):
        return '<Iteration result meta (%d, %d)>' % (
            self.iteration_result_id,
            self.meta_id,
        )


class Meta(Base):
    __tablename__ = 'meta'
    meta_id = Column(Integer, primary_key=True)
    meta_type_id = Column(Integer)
    name = Column(String)
    serial = Column(Integer)
    value = Column(String)
    comment = Column(String)

    def __init__(self, meta_type_id, name, serial, value, comment):
        self.meta_type_id = meta_type_id
        self.name = name
        self.serial = serial
        self.value = value
        self.comment = comment

    def __repr__(self):
        return '<Meta (%d: %d %s, %d, %s, %s)>' % (
            self.meta_id,
            self.meta_type_id,
            self.name,
            self.serial,
            self.value,
            self.comment,
        )


class MetaType(Base):
    __tablename__ = 'meta_type'
    meta_type_id = Column(Integer, primary_key=True)
    name = Column(String)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return '<Meta type (%d: %s)>' % (self.meta_type_id, self.name)


class MetaReference(Base):
    __tablename__ = 'meta_reference'
    meta_reference_id = Column(Integer, primary_key=True)
    meta_id = Column(Integer)
    reference_id = Column(Integer)

    def __init__(self, meta_id, reference_id):
        self.meta_id = meta_id
        self.reference_id = reference_id

    def __repr__(self):
        return '<Meta reference (%d: %d, %d)>' % (
            self.meta_reference_id,
            self.meta_id,
            self.reference_id,
        )


class Reference(Base):
    __tablename__ = 'reference'
    reference_id = Column(Integer, primary_key=True)
    name = Column(String)
    reference_type_id = Column(Integer)
    value = Column(String)

    def __init__(self, name, type_id, value):
        self.name = name
        self.type_id = type_id
        self.value = value

    def __repr__(self):
        return '<Reference (%d: %s, %d, %s)>' % (
            self.reference_id,
            self.name,
            self.type_id,
            self.value,
        )


class ReferenceType(Base):
    __tablename__ = 'reference_type'
    reference_type_id = Column(Integer, primary_key=True)
    name = Column(String)
    uri = Column(String)

    def __init__(self, name, uri):
        self.name = name
        self.uri = uri

    def __repr__(self):
        return '<Reference type (%d: %s, %s)>' % (self.reference_type_id, self.name, self.uri)


def main():
    engine = create_engine('postgresql://admin:admin@localhost:5432/bublik')
    sessionmaker(bind=engine)

    metadata = Base.metadata

    metadata.create_all(engine)


main()

# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import Counter
from datetime import datetime, timedelta
import json
import logging

from bublik.core.shortcuts import serialize
from bublik.data.models import (
    ChartView,
    ChartViewType,
    Measurement,
    MeasurementResult,
    Meta,
)
from bublik.data.serializers import (
    MeasurementSerializer,
    MetaResultSerializer,
    ViewSerializer,
)


logger = logging.getLogger('bublik.server')


class Saver:
    '''If a class describes the last level of nesting, it probably wants
    to save data to the database.  This class must be a child of
    the Saver class, It should also implement the save() method.
    '''

    measurements = []

    def __init__(self, metas):
        self.metas = metas

    def save(self):
        msg = 'Subclass must implement abstract method'
        raise NotImplementedError(msg)

    @staticmethod
    def check_metas_in_measurement(measurement, metas_ids):
        is_exist = True
        for i in metas_ids:
            is_exist &= measurement.metas.filter(id=i).exists()
        return is_exist


class InstanceLevel:
    '''The MI log consists of several levels of nesting. The nesting level
    is what is inside the parentheses.
    This class is the base class for describing the level.
    '''

    def __init__(self, data, meta_type):
        self.data = []
        for key, value in data.items():
            self.process_value(key, value, meta_type)

    def process_value(self, key, value, meta_type):
        if not isinstance(value, (int, float, str)):
            msg = f'Unexpected value type for "{key}"'
            raise TypeError(msg)

        self.data.append({'name': key, 'type': meta_type, 'value': value})

    def process_dict(self, key, value, meta_type):
        if not isinstance(value, dict):
            msg = f'Unexpected value type for "{key}"'
            raise TypeError(msg)

        for k, v in value.items():
            self.process_value(k, v, meta_type)

    def get_list_of_meta_objects(self):
        metas = []
        for d in self.data:
            meta = Meta.objects.filter(**d)
            metas.append(meta)

        return metas

    @staticmethod
    def pop(data, key, required):
        try:
            value = data.pop(key)
            if value is None:
                msg = f'there is None value by key "{key}"'
                raise ValueError(msg)
            return value
        except KeyError:
            if required:
                msg = f'the required key "{key}" is missing'
                raise KeyError(msg) from KeyError

    def __str__(self):
        res_str = ''
        for d in self.data:
            if d['name'] == 'name':
                res_str += d['value']

        return res_str


class CommonLevel(InstanceLevel):
    key_dict_to_meta_type = {
        'keys': 'measurement_key',
        'comments': 'measurement_comment',
    }

    key_to_meta_type = {
        'tool': 'tool',
        'type': 'note',
        'version': 'note',
    }

    required_constants = {'type': 'measurement', 'version': '1'}

    def __init__(self, mi_log):
        self.is_mi_log_valid(mi_log)

        self.data = []
        for key, value in mi_log.items():
            if key in self.key_dict_to_meta_type:
                meta_type = self.key_dict_to_meta_type.get(key)
                self.process_dict(key, value, meta_type)
            elif key in self.key_to_meta_type:
                meta_type = self.key_to_meta_type.get(key)
                self.process_value(key, value, meta_type)
            else:
                msg = f'Unexpected key "{key}"'
                raise KeyError(msg)

    def is_mi_log_valid(self, mi_log):
        for key, value in self.required_constants.items():
            mi_log_value = mi_log.get(key, None)
            if mi_log_value is None:
                msg = f'There is no required key "{key}"'
                raise KeyError(msg)
            if str(mi_log_value) != value:
                msg = f'Unexpected value of "{mi_log_value}" by "{key}" key'
                raise ValueError(msg)


class ResultLevel(InstanceLevel):
    meta_type = 'measurement_subject'

    def __init__(self, result, parent: CommonLevel):
        self.parent = parent
        super().__init__(result, self.meta_type)


class EntryLevel(InstanceLevel, Saver):
    meta_type = 'measurement_subject'
    counter = Counter()

    def __init__(self, entry, serial, parent: ResultLevel):
        value = InstanceLevel.pop(entry, 'value', True)
        self.serial = serial
        self.parent = parent
        self.value = value

        InstanceLevel.__init__(self, entry, self.meta_type)
        Saver.__init__(self, self.parent.parent.data + self.parent.data + self.data)

    def save(self, test_iter_result):
        self.counter['meas_obj'] += 1

        measure_serializer = serialize(MeasurementSerializer, {'metas': self.metas}, logger)

        measurement, created = measure_serializer.get_or_create()
        if created:
            self.counter['created_meas_obj'] += 1

        if measurement not in self.measurements:
            self.measurements.append(measurement)

        try:
            mr = MeasurementResult.objects.get(
                measurement=measurement,
                result=test_iter_result,
                serial=self.serial,
            )
            if mr.value != self.value:
                logger.warning(
                    'a new value was obtained from imported logs for the exist '
                    f'MeasurementResult object (id = {mr.id}). '
                    f'Old value is {mr.value}. New value is {self.value}.',
                )
                MeasurementResult.objects.filter(
                    measurement=measurement,
                    result=test_iter_result,
                    serial=self.serial,
                ).update(value=self.value)
        except Exception:
            MeasurementResult.objects.create(
                measurement=measurement,
                result=test_iter_result,
                serial=self.serial,
                value=self.value,
            )
            self.counter['created_meas_res_obj'] += 1


class ViewLevel(InstanceLevel):
    meta_type = 'measurement_view'

    def __init__(self, view):
        super().__init__(view, self.meta_type)


class ViewValueLevel(InstanceLevel, Saver):
    def __init__(self, value, parent: ViewLevel):
        self.find_measurement = None
        self.find_measurement_count = 0
        self.parent = parent

        InstanceLevel.__init__(self, value, 'measurement_subject')
        Saver.__init__(self, parent.data)

    def save(self, test_iter_result, view_type):
        if self.find_measurement_count == 0 and self.find_measurement is not None:
            msg = f'Failed to found result for {self.parent} for {view_type}'
            raise ValueError(msg)

        if self.find_measurement_count > 1:
            msg = f'More than one result found for {self.parent} for {view_type}'
            raise ValueError(msg)

        view_serializer = serialize(ViewSerializer, {'metas': self.metas}, logger)
        view, _ = view_serializer.get_or_create()

        ChartView.objects.create(
            view=view,
            measurement=self.find_measurement,
            result=test_iter_result,
            type=ChartViewType.conv(view_type),
        )


class ViewAxisXLevel(ViewValueLevel):
    def save(self, test_iter_result):
        if len(self.data) == 1 and self.data[0]['value'] == 'auto-seqno':
            self.find_measurement = None
        else:
            metas = self.get_list_of_meta_objects()

            ids = [m.id for meta in metas for m in meta]
            for m in self.measurements[:]:
                if Saver.check_metas_in_measurement(m, ids):
                    self.find_measurement_count += 1
                    self.find_measurement = m
                    self.measurements_copy.remove(m)

        ViewValueLevel.save(self, test_iter_result, 'axis_x')


class ViewAxisYLevel(ViewValueLevel):
    def __init__(self, value, parent: ViewLevel):
        if isinstance(value, Measurement):
            self.parent = parent
            self.find_measurement = value
            self.find_measurement_count = 1
            Saver.__init__(self, parent.data)
        else:
            super().__init__(value, parent)

    def save(self, test_iter_result):
        if self.find_measurement is None:
            metas = self.get_list_of_meta_objects()
            ids = [m.id for meta in metas for m in meta]
            for m in self.measurements:
                if Saver.check_metas_in_measurement(m, ids):
                    self.find_measurement = m
                    self.find_measurement_count += 1

        ViewValueLevel.save(self, test_iter_result, 'axis_y')


class ViewPointLevel(ViewValueLevel):
    def save(self, test_iter_result):
        metas = self.get_list_of_meta_objects()
        ids = [m.id for meta in metas for m in meta]
        for m in self.measurements:
            if Saver.check_metas_in_measurement(m, ids):
                self.find_measurement = m
                self.find_measurement_count += 1

        ViewValueLevel.save(self, test_iter_result, 'point')


class HandlerArtifacts:
    handle_meas_time = timedelta()

    def __init__(self, test_iter_result):
        self.test_iter_result = test_iter_result

    def handle(self, artifacts):
        #
        # After fix in script/xml_log_parse
        # this function will be changed.
        #
        for artifact in artifacts:
            try:
                mi_log = json.loads(artifact)
                self.handle_mi_artifact(mi_log)
            except json.decoder.JSONDecodeError:
                self.handle_artifact(artifact)

    def handle_artifact(self, artifact):
        m_data = {'type': 'artifact', 'value': artifact}
        mr_serializer = serialize(
            MetaResultSerializer,
            {'meta': m_data, 'result': self.test_iter_result.id},
            logger,
        )
        mr_serializer.get_or_create()

    def handle_existing_mi_artifact(self, artifact):
        try:
            self.handle_mi_artifact(artifact)
        except (KeyError, ValueError) as e:
            logger.error(e)
            msg = 'Invalid MI log format'
            raise ValueError(msg) from ValueError

    def handle_views(self, views):
        try:
            for view in views:
                view_type = view.get('type')
                if view_type == 'point':
                    value = InstanceLevel.pop(view, 'value', True)
                elif view_type == 'line-graph':
                    axis_x = InstanceLevel.pop(view, 'axis_x', True)
                    axis_y = InstanceLevel.pop(view, 'axis_y', False)
                else:
                    msg = 'Unsupported view type'
                    raise ValueError(msg)

                viewlvl = ViewLevel(view)
                if view_type == 'point':
                    point_lvl = ViewPointLevel(value, viewlvl)
                    point_lvl.save(self.test_iter_result)
                else:
                    axis_x_lvl = ViewAxisXLevel(axis_x, viewlvl)
                    Saver.measurements_copy = Saver.measurements.copy()
                    axis_x_lvl.save(self.test_iter_result)
                    if axis_y is not None:
                        for y in axis_y:
                            axis_y_lvl = ViewAxisYLevel(y, viewlvl)
                            axis_y_lvl.save(self.test_iter_result)
                    else:
                        for y in Saver.measurements_copy:
                            axis_y_lvl = ViewAxisYLevel(y, viewlvl)
                            axis_y_lvl.save(self.test_iter_result)
        except Exception as e:
            logger.error(e)
            logger.error('Failed to process views')

    def handle_mi_artifact(self, mi_log):
        start_time = datetime.now()
        try:
            results = InstanceLevel.pop(mi_log, 'results', True)
            views = InstanceLevel.pop(mi_log, 'views', False)
            commonlvl = CommonLevel(mi_log)

            for result in results:
                entries = InstanceLevel.pop(result, 'entries', True)
                resultlvl = ResultLevel(result, commonlvl)
                for serial, entry in enumerate(entries):
                    try:
                        entrylvl = EntryLevel(entry, serial, resultlvl)
                        entrylvl.save(self.test_iter_result)
                    except KeyError as ke:
                        ke = str(ke).replace("'", '')
                        logger.error(f'invalid MI log format: {ke}')
                    except ValueError as ve:
                        ve = str(ve).replace("'", '')
                        logger.warning(f'{ve}. Check your MI logs.')

            if views is not None:
                self.handle_views(views)

            HandlerArtifacts.handle_meas_time += datetime.now() - start_time

        except Exception as e:
            e = str(e).replace("'", '')
            logger.error(
                f'invalid MI log format: {e}. Handling of the current MI log is completed '
                f'without saving.',
            )
        finally:
            Saver.measurements.clear()

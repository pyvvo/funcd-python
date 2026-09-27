from __future__ import annotations
VERSION = '2.21.2'
from decimal import Decimal
from fastjsonschema import JsonSchemaValueException
NoneType = type(None)

def _funcd_i_validate(data, custom_formats={}, name_prefix=None):
    if not isinstance(data, dict):
        raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must be object', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The event payload (all keys optional). ``count`` raises the INFO burst beyond the 100 floor.', 'properties': {'count': {'title': 'Count', 'type': 'integer'}}, 'title': 'FuncInput', 'type': 'object', 'additionalProperties': False}, rule='type')
    data_is_dict = isinstance(data, dict)
    if data_is_dict:
        data_keys = set(data.keys())
        if 'count' in data_keys:
            data_keys.remove('count')
            data__count = data['count']
            if not isinstance(data__count, int) and (not (isinstance(data__count, float) and data__count.is_integer())) or isinstance(data__count, bool):
                raise JsonSchemaValueException('' + (name_prefix or 'data') + '.count must be integer', value=data__count, name='' + (name_prefix or 'data') + '.count', definition={'title': 'Count', 'type': 'integer'}, rule='type')
        if data_keys:
            raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must not contain ' + str(data_keys) + ' properties', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The event payload (all keys optional). ``count`` raises the INFO burst beyond the 100 floor.', 'properties': {'count': {'title': 'Count', 'type': 'integer'}}, 'title': 'FuncInput', 'type': 'object', 'additionalProperties': False}, rule='additionalProperties')
    return data

def __funcd_validate_input(d):
    try:
        _funcd_i_validate(d)
        return []
    except JsonSchemaValueException as e:
        return [str(e)]
VERSION = '2.21.2'
from decimal import Decimal
from fastjsonschema import JsonSchemaValueException
NoneType = type(None)

def _funcd_o_validate(data, custom_formats={}, name_prefix=None):
    if not isinstance(data, dict):
        raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must be object', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The 200 body — how many records this invocation emitted (the number the host should capture).', 'properties': {'emitted': {'title': 'Emitted', 'type': 'integer'}}, 'required': ['emitted'], 'title': 'FuncOutput', 'type': 'object', 'additionalProperties': False}, rule='type')
    data_is_dict = isinstance(data, dict)
    if data_is_dict:
        data__missing_keys = set(['emitted']) - data.keys()
        if data__missing_keys:
            raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must contain ' + (str(sorted(data__missing_keys)) + ' properties'), value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The 200 body — how many records this invocation emitted (the number the host should capture).', 'properties': {'emitted': {'title': 'Emitted', 'type': 'integer'}}, 'required': ['emitted'], 'title': 'FuncOutput', 'type': 'object', 'additionalProperties': False}, rule='required')
        data_keys = set(data.keys())
        if 'emitted' in data_keys:
            data_keys.remove('emitted')
            data__emitted = data['emitted']
            if not isinstance(data__emitted, int) and (not (isinstance(data__emitted, float) and data__emitted.is_integer())) or isinstance(data__emitted, bool):
                raise JsonSchemaValueException('' + (name_prefix or 'data') + '.emitted must be integer', value=data__emitted, name='' + (name_prefix or 'data') + '.emitted', definition={'title': 'Emitted', 'type': 'integer'}, rule='type')
        if data_keys:
            raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must not contain ' + str(data_keys) + ' properties', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The 200 body — how many records this invocation emitted (the number the host should capture).', 'properties': {'emitted': {'title': 'Emitted', 'type': 'integer'}}, 'required': ['emitted'], 'title': 'FuncOutput', 'type': 'object', 'additionalProperties': False}, rule='additionalProperties')
    return data

def __funcd_validate_output(d):
    try:
        _funcd_o_validate(d)
        return []
    except JsonSchemaValueException as e:
        return [str(e)]
import logging
from typing import TypedDict
from funcd_shim import CloudEvent, FunctionContext
log = logging.getLogger('log-burst')
_BASE_INFO = 90
_WARNINGS = 7
_ERRORS = 3

def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    """Emit a burst of ≥ 100 structured log records, then report the count."""
    data = event.get('data') or {}
    extra_info = max(int(data.get('count', 0)), 0)
    info_count = _BASE_INFO + extra_info
    emitted = 0
    for i in range(info_count):
        log.info('processing item %d', i, extra={'item': i, 'phase': 'scan'})
        emitted += 1
    for i in range(_WARNINGS):
        log.warning('slow item %d took %dms', i, 120 + i, extra={'item': i, 'phase': 'scan'})
        emitted += 1
    for i in range(_ERRORS):
        log.error('item %d failed validation', i, extra={'item': i, 'phase': 'validate'})
        emitted += 1
    return {'emitted': emitted}
from __future__ import annotations
VERSION = '2.21.2'
from decimal import Decimal
from fastjsonschema import JsonSchemaValueException
NoneType = type(None)

def _funcd_i_validate(data, custom_formats={}, name_prefix=None):
    if not isinstance(data, dict):
        raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must be object', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The event payload — a closed record carrying the counter name to increment.', 'properties': {'name': {'title': 'Name', 'type': 'string'}}, 'required': ['name'], 'title': 'FuncInput', 'type': 'object', 'additionalProperties': False}, rule='type')
    data_is_dict = isinstance(data, dict)
    if data_is_dict:
        data__missing_keys = set(['name']) - data.keys()
        if data__missing_keys:
            raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must contain ' + (str(sorted(data__missing_keys)) + ' properties'), value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The event payload — a closed record carrying the counter name to increment.', 'properties': {'name': {'title': 'Name', 'type': 'string'}}, 'required': ['name'], 'title': 'FuncInput', 'type': 'object', 'additionalProperties': False}, rule='required')
        data_keys = set(data.keys())
        if 'name' in data_keys:
            data_keys.remove('name')
            data__name = data['name']
            if not isinstance(data__name, str):
                raise JsonSchemaValueException('' + (name_prefix or 'data') + '.name must be string', value=data__name, name='' + (name_prefix or 'data') + '.name', definition={'title': 'Name', 'type': 'string'}, rule='type')
        if data_keys:
            raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must not contain ' + str(data_keys) + ' properties', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The event payload — a closed record carrying the counter name to increment.', 'properties': {'name': {'title': 'Name', 'type': 'string'}}, 'required': ['name'], 'title': 'FuncInput', 'type': 'object', 'additionalProperties': False}, rule='additionalProperties')
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
        raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must be object', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The 200 body — the name and its new count.', 'properties': {'name': {'title': 'Name', 'type': 'string'}, 'count': {'title': 'Count', 'type': 'integer'}}, 'required': ['name', 'count'], 'title': 'FuncOutput', 'type': 'object', 'additionalProperties': False}, rule='type')
    data_is_dict = isinstance(data, dict)
    if data_is_dict:
        data__missing_keys = set(['name', 'count']) - data.keys()
        if data__missing_keys:
            raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must contain ' + (str(sorted(data__missing_keys)) + ' properties'), value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The 200 body — the name and its new count.', 'properties': {'name': {'title': 'Name', 'type': 'string'}, 'count': {'title': 'Count', 'type': 'integer'}}, 'required': ['name', 'count'], 'title': 'FuncOutput', 'type': 'object', 'additionalProperties': False}, rule='required')
        data_keys = set(data.keys())
        if 'name' in data_keys:
            data_keys.remove('name')
            data__name = data['name']
            if not isinstance(data__name, str):
                raise JsonSchemaValueException('' + (name_prefix or 'data') + '.name must be string', value=data__name, name='' + (name_prefix or 'data') + '.name', definition={'title': 'Name', 'type': 'string'}, rule='type')
        if 'count' in data_keys:
            data_keys.remove('count')
            data__count = data['count']
            if not isinstance(data__count, int) and (not (isinstance(data__count, float) and data__count.is_integer())) or isinstance(data__count, bool):
                raise JsonSchemaValueException('' + (name_prefix or 'data') + '.count must be integer', value=data__count, name='' + (name_prefix or 'data') + '.count', definition={'title': 'Count', 'type': 'integer'}, rule='type')
        if data_keys:
            raise JsonSchemaValueException('' + (name_prefix or 'data') + ' must not contain ' + str(data_keys) + ' properties', value=data, name='' + (name_prefix or 'data') + '', definition={'description': 'The 200 body — the name and its new count.', 'properties': {'name': {'title': 'Name', 'type': 'string'}, 'count': {'title': 'Count', 'type': 'integer'}}, 'required': ['name', 'count'], 'title': 'FuncOutput', 'type': 'object', 'additionalProperties': False}, rule='additionalProperties')
    return data

def __funcd_validate_output(d):
    try:
        _funcd_o_validate(d)
        return []
    except JsonSchemaValueException as e:
        return [str(e)]
from typing import TypedDict
from funcd_shim import CloudEvent, FunctionContext

def handle(context: FunctionContext, event: CloudEvent[FuncInput]) -> FuncOutput:
    """Increment the per-name counter via the ``pycounters`` KV binding and return it."""
    assert 'data' in event
    name = event['data']['name']
    count = int(context.kv.get_str('pycounters', name) or 0) + 1
    context.kv.put('pycounters', name, str(count))
    context.log('kv-counter', name, count)
    return {'name': name, 'count': count}
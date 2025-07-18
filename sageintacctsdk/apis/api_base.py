"""
API Base class with util functions
"""
import json
import logging
import datetime
import uuid
from warnings import warn
from typing import Dict, List, Tuple
from urllib.parse import unquote
import re

import xmltodict
import requests

from ..exceptions import SageIntacctSDKError, ExpiredTokenError, InvalidTokenError, NoPrivilegeError, \
    WrongParamsError, NotFoundItemError, InternalServerError, DataIntegrityWarning
from .constants import dimensions_fields_mapping


logger = logging.getLogger(__name__)

class ApiBase:
    """The base class for all API classes."""

    def __init__(self, dimension: str = None, pagesize: int = 2000, post_legacy_method: str = None):
        self.__sender_id = None
        self.__sender_password = None
        self.__session_id = None
        self.__api_url = 'https://api.intacct.com/ia/xml/xmlgw.phtml'
        self.__dimension = dimension
        self.__pagesize = pagesize
        self.__post_legacy_method = post_legacy_method

    def set_sender_id(self, sender_id: str):
        """
        Set the sender id for APIs
        :param sender_id: sender id
        :return: None
        """
        self.__sender_id = sender_id

    def set_sender_password(self, sender_password: str):
        """
        Set the sender password for APIs
        :param sender_password: sender id
        :return: None
        """
        self.__sender_password = sender_password

    def set_show_private(self, show_private: bool):
        """
        Set the show private for APIs
        :param show_private: boolean
        :return: None
        """
        self.__show_private = show_private

    def get_session_id(self, user_id: str, company_id: str, user_password: str, entity_id: str = None):
        """
        Sets the session id for APIs
        :param access_token: acceess token (JWT)
        :return: session id
        """

        timestamp = datetime.datetime.now()
        dict_body = {
            'request': {
                'control': {
                    'senderid': self.__sender_id,
                    'password': self.__sender_password,
                    'controlid': timestamp,
                    'uniqueid': False,
                    'dtdversion': 3.0,
                    'includewhitespace': False
                },
                'operation': {
                    'authentication': {
                        'login': {
                            'userid': user_id,
                            'companyid': company_id,
                            'password': user_password,
                            'locationid': entity_id
                        }
                    },
                    'content': {
                        'function': {
                            '@controlid': str(uuid.uuid4()),
                            'getAPISession': None
                        }
                    }
                }
            }
        }

        response = self.__post_request(dict_body, self.__api_url)

        if response['authentication']['status'] == 'success':
            session_details = response['result']['data']['api']
            self.__api_url = session_details['endpoint']
            self.__session_id = session_details['sessionid']

            return self.__session_id

        else:
            raise SageIntacctSDKError('Error: {0}'.format(response['errormessage']))

    def set_session_id(self, session_id: str):
        """
        Set the session id for APIs
        :param session_id: session id
        :return: None
        """
        self.__session_id = session_id

    def __support_id_msg(self, errormessages):
        """Finds whether the error messages is list / dict and assign type and error assignment.

        Parameters:
            errormessages (dict / list): error message received from Sage Intacct.

        Returns:
            Error message assignment and type.
        """
        error = {}
        if isinstance(errormessages['error'], list):
            error['error'] = errormessages['error'][0]
            error['type'] = 'list'
        elif isinstance(errormessages['error'], dict):
            error['error'] = errormessages['error']
            error['type'] = 'dict'

        return error

    def __decode_support_id(self, errormessages):
        """Decodes Support ID.

        Parameters:
            errormessages (dict / list): error message received from Sage Intacct.

        Returns:
            Same error message with decoded Support ID.
        """
        support_id_msg = self.__support_id_msg(errormessages)
        data_type = support_id_msg['type']
        error = support_id_msg['error']
        if (error and error['description2']):
            message = error['description2']
            support_id = re.search('Support ID: (.*)]', message)
            if support_id and support_id.group(1):
                decoded_support_id = unquote(support_id.group(1))
                message = message.replace(support_id.group(1), decoded_support_id)

        # Converting dict to list even for single error response
        if data_type == 'dict':
            errormessages['error'] = [errormessages['error']]

        errormessages['error'][0]['description2'] = message if message else None

        return errormessages

    def __post_request_for_raw_response(self, dict_body: dict, api_url: str):
        """Create an HTTP post request to get a raw response.

        Parameters:
            data (dict): HTTP POST body data for the wanted API.
            api_url (str): Url for the wanted API endpoint.

        Returns:
            A requests.Response object.
        """

        api_headers = {
            'content-type': 'application/xml'
        }
        body = xmltodict.unparse(dict_body)

        return requests.post(api_url, headers=api_headers, data=body.encode('utf-8'))


    def __post_request(self, dict_body: dict, api_url: str):
        """Create an HTTP post request and handle HTTP errors.

        Parameters:
            data (dict): HTTP POST body data for the wanted API.
            api_url (str): Url for the wanted API.

        Returns:
            A response from the request (dict).
        """

        logger.debug('Payload for post request: %s', dict_body)
        raw_response = self.__post_request_for_raw_response(dict_body, api_url)
        try:
            parsed_xml = xmltodict.parse(raw_response.text, force_list={self.__dimension})
            # print(parsed_xml)
        except:
            #bad xml format from Sage Intacct fix
            raw_response = '<root>' + raw_response.text + '</root>'
            parsed_xml = xmltodict.parse(raw_response, force_list={self.__dimension})['root']
        parsed_response = json.loads(json.dumps(parsed_xml))
        # print(parsed_response)

        if raw_response.status_code == 200:
            response = parsed_response.get('response', {})
            control_status = response.get('control', {}).get('status', '')
            auth_status = response.get('operation', {}).get('authentication', {}).get('status', '')
            result_status = response.get('operation', {}).get('result', {}).get('status', '')


            if control_status == 'failure' or auth_status == 'failure' or result_status == 'failure':
                logger.info('Response for post request: %s', raw_response.text)
            else:
                logger.debug('Response for post request: %s', raw_response.text)

            if parsed_response['response']['control']['status'] == 'success':
                api_response = parsed_response['response']['operation']

            if parsed_response['response']['control']['status'] == 'failure':
                exception_msg = self.__decode_support_id(parsed_response['response']['errormessage'])

                raise WrongParamsError('Some of the parameters are wrong', exception_msg)

            if api_response['authentication']['status'] == 'failure':
                raise InvalidTokenError('Invalid token / Incorrect credentials', api_response['errormessage'])

            if api_response['result']['status'] == 'success':
                return api_response

            if api_response['result']['status'] == 'failure':
                if 'errormessage' in api_response['result']:
                    exception_msg = self.__decode_support_id(api_response['result']['errormessage'])

                    for error in exception_msg['error']:
                        if error['description2'] and 'You do not have permission for API' in error['description2']:
                            raise NoPrivilegeError('The user has insufficient privilege', exception_msg)

                    raise WrongParamsError('Error during {0}'.format(api_response['result']['function']), exception_msg)
                else:
                    custom_response = {
                        'message': 'Error during {}'.format(api_response['result']['function']),
                        'response': {
                            'error': [
                                {
                                    'errorno': '-',
                                    'description': None,
                                    'description2': 'Something went wrong',
                                    'correction': '-'
                                }
                            ]
                        }
                    }
                    raise WrongParamsError('Something went wrong', custom_response)


        logger.info('Response for post request: %s', raw_response.text)
        if 'result' in parsed_response:
            if 'errormessage' in parsed_response['result']:
                parsed_response = parsed_response['result']['errormessage']

        if 'response' in parsed_response:
            if 'errormessage' in parsed_response['response']:
                parsed_response = parsed_response['response']['errormessage']

        if raw_response.status_code == 400:
            if 'error' in parsed_response and isinstance(parsed_response['error'], dict) and 'errorno' in parsed_response['error'] and parsed_response['error']['errorno'] == 'invalidRequest':
                raise InvalidTokenError('Invalid token / Incorrect credentials', parsed_response)
            else:
                raise WrongParamsError('Some of the parameters are wrong', parsed_response)

        if raw_response.status_code == 401:
            raise InvalidTokenError('Invalid token / Incorrect credentials', parsed_response)

        if raw_response.status_code == 403:
            raise NoPrivilegeError('Forbidden, the user has insufficient privilege', parsed_response)

        if raw_response.status_code == 404:
            raise NotFoundItemError('Not found item with ID', parsed_response)

        if raw_response.status_code == 498:
            raise ExpiredTokenError('Expired token, try to refresh it', parsed_response)

        if raw_response.status_code == 500:
            raise InternalServerError('Internal server error', parsed_response)

        raise SageIntacctSDKError('Error: {0}'.format(parsed_response))

    def format_and_send_request(self, data: Dict):
        """Format data accordingly to convert them to xml.

        Parameters:
            data (dict): HTTP POST body data for the wanted API.

        Returns:
            A response from the __post_request (dict).
        """

        key = next(iter(data))
        timestamp = datetime.datetime.now()

        dict_body = {
            'request': {
                'control': {
                    'senderid': self.__sender_id,
                    'password': self.__sender_password,
                    'controlid': timestamp,
                    'uniqueid': False,
                    'dtdversion': 3.0,
                    'includewhitespace': False
                },
                'operation': {
                    'authentication': {
                        'sessionid': self.__session_id
                    },
                    'content': {
                        'function': {
                            '@controlid': str(uuid.uuid4()),
                            key: data[key]
                        }
                    }
                }
            }
        }
        if self.__show_private:
            try:
                options = {'showprivate': True}
                dict_body['request']['operation']['content']['function']['query']['options'] = options
            except KeyError:
                pass

        response = self.__post_request(dict_body, self.__api_url)
        return response['result']

    def post(self, data: Dict):
        if self.__post_legacy_method is not None:
            return self.__construct_post_legacy_payload(data)
        else:
            return self.__construct_post_payload(data)

    def update(self, data: Dict):
        '''

        :param data: A dictionary for the object (e.g., Vendor object) with information to update (dict).
        :return: A response from the __post_request (dict).
        '''

        return self.__construct_update_payload(data)

    def __construct_post_payload(self, data: Dict):
        payload = {
            'create': {
                self.__dimension: data
            }
        }

        return self.format_and_send_request(payload)

    def __construct_update_payload(self, data: Dict):
        payload = {
            'update': {
                self.__dimension: data
            }
        }

        return self.format_and_send_request(payload)

    def __construct_post_legacy_payload(self, data: Dict):
        payload = {
            self.__post_legacy_method: data
        }

        return self.format_and_send_request(payload)

    def count(self, field: str = 'STATUS', value: str = 'active'):
        get_count = {
            'readByQuery': {
                'object': self.__dimension,
                'fields': 'RECORDNO',
                'query': '1=1',
                'pagesize': '1'
            }
        }

        response = self.format_and_send_request(get_count)
        return int(response['data']['@totalcount'])

    def read_by_query(self, fields: list = None):
        """Read by Query from Sage Intacct

        Parameters:
            fields (list): Get selective fields to be returned. (optional).

        Returns:
            Dict.
        """
        payload = {
            'readByQuery': {
                'object': self.__dimension,
                'fields': ','.join(fields) if fields else '*',
                'query': None,
                'pagesize': '1000'
            }
        }

        return self.format_and_send_request(payload)

    def get(self, field: str, value: str, fields: list = None):
        """Get data from Sage Intacct based on filter.

        Parameters:
            field (str): A parameter to filter by the field. (required).
            value (str): A parameter to filter by the field - value. (required).

        Returns:
            Dict.
        """
        data = {
            'readByQuery': {
                'object': self.__dimension,
                'fields': ','.join(fields) if fields else '*',
                'query': "{0} = '{1}'".format(field, value),
                'pagesize': '1000'
            }
        }

        return self.format_and_send_request(data)['data']

    def get_all(self, field: str = '1', value: str = '1', fields: list = None):
        """Get all data from Sage Intacct using paginated readByQuery and readMore.

        Returns:
            List of Dict.
        """
        complete_data = []
        count = self.count(None)
        print(f"API says total count is: {count}")
        pagesize = self.__pagesize
        print(f"pagesize: {pagesize}")

        # Initial readByQuery request
        data = {
            'readByQuery': {
                'object': self.__dimension,
                'fields': '*',
                'pagesize': '2000',
                'query': "{0} = '{1}'".format(field, value),
            }
        }

        response = self.format_and_send_request(data)['data']

        # Find the key in response that matches self.__dimension, case-insensitively
        paginated_data = None
        for key in response:
            if key.lower() == self.__dimension.lower():
                paginated_data = response[key]
                break

        if paginated_data is None:
            print(f"Dimension {self.__dimension} not found in response, returning empty list.")
            return []

        complete_data.extend(paginated_data if isinstance(paginated_data, list) else [paginated_data])
        actual_records_processed = len(complete_data)
        print(f"Page 1: got {len(paginated_data)} records")
        print(f"Total processed so far: {actual_records_processed}")

        # Pagination using readMore and resultId
        result_id = response.get('@resultId')
        num_remaining = int(response.get('@numremaining', 0))
        page = 2

        while result_id and num_remaining > 0:
            data = {
                'readMore': {
                    'resultId': result_id
                }
            }
            response = self.format_and_send_request(data)['data']
            paginated_data = None
            for key in response:
                if key.lower() == self.__dimension.lower():
                    paginated_data = response[key]
                    break

            if paginated_data is None:
                print(f"Dimension {self.__dimension} not found in response, breaking loop.")
                break

            complete_data.extend(paginated_data if isinstance(paginated_data, list) else [paginated_data])
            actual_records_processed = len(complete_data)
            print(f"Page {page}: got {len(paginated_data)} records")
            print(f"Total processed so far: {actual_records_processed}")

            result_id = response.get('@resultId')
            num_remaining = int(response.get('@numremaining', 0))
            page += 1

        print(f"Expected {count}, actually got {len(complete_data)}")
        return complete_data

    def get_all_generator(self, field: str = None, value: str = None, fields: list = None, updated_at: str = None, order_by_field: str = None, order: str = None):
        """
        Get all data from Sage Intacct
        """
        count = self.count(None)
        pagesize = self.__pagesize
        for offset in range(0, count, pagesize):
            data = {
                'query': {
                    'object': self.__dimension,
                    'select': {
                        'field': fields if fields else dimensions_fields_mapping[self.__dimension]
                    },
                    'orderby': None,
                    'pagesize': pagesize,
                    'offset': offset,
                    'filter': None,
                }
            }

            if order_by_field and order:
                data['query']['orderby'] = {
                    'order': {
                        'field': order_by_field,
                        order: None
                    }
                }

            field_value_filter = (
                {"equalto": {"field": field, "value": value}} if field and value else {}
            )
            updated_at_filter = (
                {
                    "greaterthanorequalto": (
                        {"field": "WHENMODIFIED", "value": updated_at}
                    )
                }
                if updated_at
                else {}
            )
            # if we have updated_at_filter and field_value_filter we need to 'and' them
            if updated_at_filter and field_value_filter:
                data["query"]["filter"] = {
                    "and": {**field_value_filter, **updated_at_filter}
                }
            # if we only have field_value filter, just use it
            elif field_value_filter:
                data["query"]["filter"] = field_value_filter
            # if we only have updated_at filter, just use it
            elif updated_at_filter:
                data["query"]["filter"] = updated_at_filter

            if not data['query']['filter']:
                del data['query']['filter']
            if not data['query']['orderby']:
                del data['query']['orderby']

            response = self.format_and_send_request(data)['data']
            if self.__dimension in response:
                yield self.format_and_send_request(data)['data'][self.__dimension]
            else:
                yield []


    __query_filter = List[Tuple[str, str, str]]

    def get_by_query(self, fields: List[str] = None,
                     and_filter: __query_filter = None,
                     or_filter: __query_filter = None,
                     filter_payload: dict = None):
        """Get data from Sage Intacct using query method based on filter.

        See sage intacct documentation here for query structures:
        https://developer.intacct.com/web-services/queries/

                Parameters:
                    fields (str): A parameter to filter by the field. (required).
                    and_filter (list(tuple)): List of tuple containing (operator (str),field (str), value (str))
                    or_filter (list(tuple)): List of tuple containing (operator (str),field (str), value (str))
                    filter_payload (dict): Formatted query payload in dictionary format.
                    if 'between' operators is used on and_filter or or_filter field must be submitted as
                    [str,str]
                    if 'in' operator is used field may be submitted as [str,str,str,...]

                Returns:
                    Dict.
                """

        complete_data = []
        filtered_total = None
        count = self.count(None)
        pagesize = self.__pagesize
        offset = 0
        formatted_filter = filter_payload
        data = {
            'query': {
                'object': self.__dimension,
                'select': {
                    'field': fields if fields else dimensions_fields_mapping[self.__dimension]
                },
                'pagesize': pagesize,
                'offset': offset
            }
        }
        if and_filter and or_filter:
            formatted_filter = {'and': {}}
            for operator, field, value in and_filter:
                formatted_filter['and'].setdefault(operator, {}).update({'field': field, 'value': value})
            formatted_filter['and']['or'] = {}
            for operator, field, value in or_filter:
                formatted_filter['and']['or'].setdefault(operator, {}).update({'field': field, 'value': value})

        elif and_filter:
            if len(and_filter) > 1:
                formatted_filter = {'and': {}}
                for operator, field, value in and_filter:
                    formatted_filter['and'].setdefault(operator, []).append({'field': field, 'value': value})
            else:
                formatted_filter = {}
                for operator, field, value in and_filter:
                    formatted_filter.setdefault(operator, {}).update({'field': field, 'value': value})

        elif or_filter:
            if len(or_filter) > 1:
                formatted_filter = {'or': {}}
                for operator, field, value in or_filter:
                    formatted_filter['or'].setdefault(operator, []).append({'field': field, 'value': value})
            else:
                formatted_filter = {}
                for operator, field, value in or_filter:
                    formatted_filter.setdefault(operator, {}).update({'field': field, 'value': value})

        if formatted_filter:
            data['query']['filter'] = formatted_filter
        print(data)
        for offset in range(0, count, pagesize):
            data['query']['offset'] = offset
            paginated_data = self.format_and_send_request(data)['data']
            if self.__dimension in paginated_data:
                complete_data.extend(paginated_data[self.__dimension])
            filtered_total = int(paginated_data['@totalcount'])
            if paginated_data['@numremaining'] == '0':
                break
        if filtered_total != len(complete_data):
            warn(message='Your data may not be complete. Records returned do not equal total query record count',
                 category=DataIntegrityWarning)
        return complete_data

    def get_lookup(self):
        """ Returns all fields with attributes from the object called on.

                Parameters:
                    self
                Returns:
                    Dict.
        """

        data = {'lookup': {'object': self.__dimension}}
        return self.format_and_send_request(data)['data']

    def delete(self, key):
        """
        Delete the dimension with the given key
        """

        data = {
            'delete': {
                'object': self.__dimension,
                'keys': str(key)
            }
        }

        return self.format_and_send_request(data=data)

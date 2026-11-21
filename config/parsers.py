from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser

NOT_AN_OBJECT = 'Send the data as a JSON object.'

class ObjectJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        data = super().parse(stream, media_type, parser_context)
        if not isinstance(data, dict):
            raise ParseError(NOT_AN_OBJECT)
        return data

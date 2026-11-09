"""Request body parsers (REST_FRAMEWORK DEFAULT_PARSER_CLASSES)."""
from rest_framework.exceptions import ParseError
from rest_framework.parsers import JSONParser

NOT_AN_OBJECT = 'Send the data as a JSON object.'


class ObjectJSONParser(JSONParser):
    """
    JSON, but only an object: every endpoint reads named fields, so a list or a plain value would
    crash it (500). Those get 400 `parse_error` instead, before any view runs.
    """

    def parse(self, stream, media_type=None, parser_context=None):
        data = super().parse(stream, media_type, parser_context)
        if not isinstance(data, dict):
            raise ParseError(NOT_AN_OBJECT)
        return data

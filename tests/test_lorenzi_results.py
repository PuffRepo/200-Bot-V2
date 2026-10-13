"""Lorenzi transport tests use mocks; the release suite requires no network."""
from io import BytesIO
from unittest.mock import MagicMock,patch
import unittest
from urllib.parse import parse_qs,urlsplit
from PIL import Image
from v2 import table_image

class LorenziResultsTest(unittest.TestCase):
    def setUp(self):
        self.rows=[{'name':f'LAB{i:02d}'} for i in range(1,13)]
        self.scores=' '.join(f"{r['name']} 82" for r in self.rows)
        stream=BytesIO();Image.new('RGB',(860,520),'white').save(stream,format='PNG');self.png=stream.getvalue()

    def response(self,payload,status=200):
        response=MagicMock();response.__enter__.return_value=response
        response.status=status;response.read.return_value=payload
        return response

    def test_original_query_grammar_all_formats(self):
        for fmt in (1,2,3,4,6):
            query=table_image.lorenzi_query(fmt,self.scores,self.rows)
            self.assertEqual(sum(line.startswith('LAB') for line in query.splitlines()),12)
            if fmt==1:self.assertTrue(query.startswith('-\n'))
            else:self.assertEqual(query.count('1 #'),12//fmt)
            self.assertNotIn('[',query)  # No guessed countries.

    def test_valid_service_png_preserved_and_request_encoded(self):
        with patch.object(table_image,'urlopen',return_value=self.response(self.png)) as request:
            image=table_image.render_results(1,3,'tier-all',self.scores,self.rows)
        self.assertEqual(image,self.png)
        call=request.call_args
        self.assertEqual(call.kwargs['timeout'],4)
        url=call.args[0].full_url
        self.assertEqual(urlsplit(url).netloc,'gb.hlorenzi.com')
        self.assertEqual(parse_qs(urlsplit(url).query)['data'][0],table_image.lorenzi_query(3,self.scores,self.rows))

    def test_timeout_invalid_image_and_http_error_fall_back(self):
        cases=[TimeoutError(),self.response(b'<html>unavailable</html>'),self.response(self.png,503),self.response(b'\x89PNG\r\n\x1a\ntruncated')]
        for case in cases:
            with self.subTest(case=type(case).__name__):
                arguments={'side_effect':case} if isinstance(case,Exception) else {'return_value':case}
                with patch.object(table_image,'urlopen',**arguments),patch.object(table_image,'render_results_local',return_value=b'local-png') as fallback,self.assertLogs('v2.table_image',level='WARNING'):
                    result=table_image.render_results(12,3,'tier-all',self.scores,self.rows,'10 Oct 2026')
                self.assertEqual(result,b'local-png')
                fallback.assert_called_once_with(12,3,'tier-all',self.scores,self.rows,'10 Oct 2026')

    def test_oversized_download_is_bounded_and_falls_back(self):
        response=self.response(b'x'*(table_image.LORENZI_MAX_BYTES+1))
        with patch.object(table_image,'urlopen',return_value=response),patch.object(table_image,'render_results_local',return_value=b'local'),self.assertLogs('v2.table_image',level='WARNING'):
            self.assertEqual(table_image.render_results(1,1,'tier-all',self.scores,self.rows),b'local')
        response.read.assert_called_once_with(table_image.LORENZI_MAX_BYTES+1)

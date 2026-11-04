import base64,io,sys,unittest,zipfile
sys.path.insert(0,'src')
from word import extract_docx

def package(xml):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z: z.writestr('word/document.xml',xml)
 return base64.b64encode(out.getvalue()).decode()

class WordTests(unittest.TestCase):
 def test_paragraph_table_and_breaks(self):
  xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>报销制度</w:t><w:tab/><w:t>30天</w:t><w:br/><w:t>请提交</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格条款</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'''
  self.assertEqual(extract_docx(package(xml)),'报销制度\t30天\n请提交\n表格条款')
 def test_bad_files(self):
  for data in ('not base64!',base64.b64encode(b'not a zip').decode(),package('<invalid/>'),package('<!DOCTYPE a [<!ENTITY x "a">]><a/>')):
   with self.subTest(data=data[:20]),self.assertRaises(ValueError): extract_docx(data)
 def test_empty_doc(self):
  with self.assertRaises(ValueError): extract_docx(package('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>'))
 def test_large_input(self):
  with self.assertRaises(ValueError): extract_docx('A'*6000000)

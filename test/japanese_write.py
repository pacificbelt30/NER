import os
import sys
sys.path.append(os.path.abspath(''))
print(sys.path)
from ner import PaperExtract
from ner import config_load

filename = './pdf/test5.pdf'
conf = config_load('./config.yml')
if len(sys.argv) >= 2:
    filename = sys.argv[1]
pe = PaperExtract(path=filename,font=conf['FONT'])
pe.extract_textinfo()
pe.fill_all_blocks()

with open("test_page_eng.txt","w") as f:
    for txt in pe.get_page_text_array(0):
        print(txt,file=f)

pe.visualize_blocks()

array=[]
for i in range(pe.page_count):
    array.append(len(pe.get_page_text_array(i)))
print(array)

import sys
import os
import json
from ner import TextraConnection

class Block():
    def __init__(self,filename):
        self.split_marker = '------------------------------'
        try:
            with open(filename,'r') as f:
                self.txt = f.read()
        except:
            import traceback
            traceback.print_exc()
            self.txt = ''
        self.translate_api = TextraConnection(filename)
        self.split_txt = self.split_array(self.txt)

    def split_array(self,txt):
        return txt.split(self.split_marker)[:-1]

    def translate(self):
        result_txt = ''
        for txt in self.split_txt:
            result_txt+=self.translate_api._post_block(txt)+'\n'+self.split_marker+'\n'
        print(result_txt)
        self.previous_result = result_txt[:-2]
        return result_txt[:-2]

    def save(self,filename):
        try:
            with open(filename,'w') as f:
                print(self.previous_result,file=f)
        except:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    filename = ''
    if len(sys.argv) >= 2:
        filename = sys.argv[1]
    b = Block(filename)
    b.translate()
    b.save('new_'+filename)



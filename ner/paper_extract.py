import fitz
import os
import sys
import re
from nltk.tokenize import sent_tokenize
from .yml_load import config_load

fitz.TOOLS.set_aa_level(5)

class PaperExtract:
    def __init__(self,path='',font='') -> None:
        self.DEBUG = False
        self.path = path
        self.blocks = []
        self.doc = self._open()
        self.new_doc = self._open()

        # use Built-in japanese font if font == ''
        font = os.path.expanduser(font) if font != '' else None
        self.font = fitz.Font(fontname="japan", fontfile=font, language="ja")
        print(self.font)

    @property
    def page_count(self) -> int:
        return self.doc.page_count
    @property
    def meta_data(self):
        return self.doc.metadata
    @property
    def output_path(self) -> str:
        split_filename = os.path.splitext(os.path.basename(self.path))
        ext = ''
        for i in range(1,len(split_filename)):
            ext += split_filename[i]
        new_filename = split_filename[0] + "_new" + ext
        new_path = os.path.join(os.path.dirname(self.path),new_filename)
        return new_path
    @property
    def output_visualize_path(self) -> str:
        split_filename = os.path.splitext(os.path.basename(self.path))
        ext = ''
        for i in range(1,len(split_filename)):
            ext += split_filename[i]
        new_filename = split_filename[0] + "_vi" + ext
        new_path = os.path.join(os.path.dirname(self.path),new_filename)
        return new_path

    def _open(self) -> fitz.Document:
        try:
            return fitz.open(self.path)
        except:
            import traceback
            traceback.print_exc()
            return fitz.Document()

    def extract_textinfo(self):
        self.pages_info = []
        for page_num in range(self.page_count):
            blocks = []
            page = self.doc.load_page(page_num)
            # dict-format: https://pymupdf.readthedocs.io/en/latest/app1.html
            fdict = page.get_text("dict",sort=False)

            for block in fdict['blocks']:
                # 文字以外は無視
                if not block['type'] == 0:
                    continue

                fontsize_array = []
                full = ''
                # bbox = []
                for line in block['lines']:
                    span_txt = ''
                    # bbox.append(line['spans'][0]['bbox'][0])
                    for txt in line['spans']:
                        span_txt += txt['text']
                        fontsize_array.append(txt['size'])
                    full += re.sub('- $','',span_txt+" ")
                # print(full)

                # 記号のみのブロックとかを弾きたい
                threshold = 0.5 # アルファベットの占有率が閾値に満たない場合に訳さないことにする
                filt_txt = re.sub(r"[^a-zA-Z]","",full) # アルファベット以外の文字列を消す
                if filt_txt == "":
                    if self.DEBUG: print('This block is not sentenses.',full)
                    continue
                elif len(filt_txt)/len(full) <= 0.6:
                    # print(len(filt_txt),len(full))
                    # print(filt_txt,full)
                    if self.DEBUG: print('This block is not sentenses.',full)
                    continue
                elif len(filt_txt)<=40:
                    if self.DEBUG: print('This block is too short.',full)
                    continue

                # 新しいブロックとして追加追加
                blocks.append({})
                blocks[len(blocks)-1]['bbox'] = block['bbox']
                blocks[len(blocks)-1]['fontsize'] = 11
                blocks[len(blocks)-1]['page'] = 0
                blocks[len(blocks)-1]['fontsize'] = sum(fontsize_array)//len(fontsize_array)
                blocks[len(blocks)-1]['txt'] = full
            self.pages_info.append(blocks)

    def extract_pageinfo(self,page):
        pass
    
    # 各文は改行で区切られる
    def get_page_text_array(self,page=0,is_split=True) -> list:
        fulltext_array = []
        for block in self.pages_info[page]:
            if is_split:
                txt = ''
                for sentennse in sent_tokenize(block['txt']):
                    txt += sentennse+'\n'
                fulltext_array.append(txt)
            else: fulltext_array.append(block['txt'])
        return fulltext_array

    # 各文は改行で区切られる
    def get_all_text_array(self,is_split=True) -> list:
        fulltext_array = []
        for page in self.pages_info:
            for block in page:
                # fulltext_array.append(block['txt'])
                if is_split:
                    txt = ''
                    for sentennse in sent_tokenize(block['txt']):
                        txt += sentennse+'\n'
                    fulltext_array.append(txt)
                else: fulltext_array.append(block['txt'])
        return fulltext_array

    def get_page_text(self,page=0) -> str:
        fulltext = ''
        for block in self.pages_info[page]:
            fulltext += block['txt'] + "\n------------------------------\n"
        return fulltext

    def get_all_text(self) -> str:
        fulltext = ''
        for page in self.pages_info:
            for block in page:
                fulltext += block['txt'] + "\n------------------------------\n"
        return fulltext

    def fill_all_blocks(self):
        for page_num in range(self.new_doc.page_count):
            page = self.new_doc.load_page(page_num)
            # print(page.get_text("text"))
            for block in self.pages_info[page_num]:
                annot = page.add_redact_annot(block['bbox'])
                annot.update()
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

    def visualize_blocks(self):
        temp = self._open()
        for page_num in range(temp.page_count):
            page = temp.load_page(page_num)
            # print(page.get_text("text"))
            for block in self.pages_info[page_num]:
                shape=page.new_shape()
                shape.draw_rect(block['bbox'])
                shape.finish(color=(0,0,0),fill=None)
                shape.commit()
        temp.ez_save(self.output_visualize_path)

    def insert_txt(self,page_num,txt) -> bool:
        txt_array = txt.split('\n------------------------------\n')
        txt_array.pop(len(txt_array)-1)
        # print(txt_array)
        # return True
        if len(self.pages_info[page_num]) != len(txt_array):
            print('THE PAGE AND TEXT INFORMATION DO NOT MATCH')
            print(len(self.pages_info[page_num]),"!=",len(txt_array))
            return False
        page = self.new_doc.load_page(page_num)
        for i in range(len(txt_array)):
            itxt = txt_array[i]
            itxt = re.sub('\n(?!\n)','',itxt)
            # tw.fill_textbox(self.pages_info[page_num][i]['bbox'],txt_array[i],font=self.font,fontsize=self.pages_info[page_num][i]['fontsize']-0.4)
            fontsize = self.calc_fontsize(page.rect,self.pages_info[page_num][i]['bbox'],itxt,self.font,self.pages_info[page_num][i]['fontsize'])
            print(fontsize)
            tw = fitz.TextWriter(page.rect)
            # tw.fill_textbox(self.pages_info[page_num][i]['bbox'],itxt,font=self.font,fontsize=self.pages_info[page_num][i]['fontsize']-0.4)
            tw.fill_textbox(self.pages_info[page_num][i]['bbox'],itxt,font=self.font,fontsize=fontsize)
            tw.write_text(page)
        return True
    
    def calc_fontsize(self,pagerect,rect,text,font,fontsize):
        last_fontsize = fontsize*1.5
        while True:
            last_fontsize -= 0.5
            tw = fitz.TextWriter(pagerect)
            # print(pagerect,rect)
            try:
                overflow = tw.fill_textbox(rect,text,font=font,fontsize=last_fontsize,warn=True)
            except:
                return fontsize
            # print('overflow:', overflow)
            if overflow == []:
                break
        return last_fontsize

    def insert_txt_from_array(self,page_num,txt_array) -> bool:
        if len(self.pages_info[page_num]) != len(txt_array):
            print('THE PAGE AND TEXT INFORMATION DO NOT MATCH')
            print(len(self.pages_info[page_num]),"!=",len(txt_array))
            return False
        page = self.new_doc.load_page(page_num)
        tw = fitz.TextWriter(page.rect)
        for i in range(len(txt_array)):
            itxt = txt_array[i]
            itxt = re.sub('\n(?!\n)','',itxt)
            fontsize = self.calc_fontsize(page.rect,self.pages_info[page_num][i]['bbox'],itxt,self.font,self.pages_info[page_num][i]['fontsize'])
            print(fontsize)
            # tw.fill_textbox(self.pages_info[page_num][i]['bbox'],itxt,font=self.font,fontsize=self.pages_info[page_num][i]['fontsize']-0.4)
            # tw = fitz.TextWriter(self.pages_info[page_num][i]['bbox'])
            tw.fill_textbox(self.pages_info[page_num][i]['bbox'],itxt,font=self.font,fontsize=fontsize)
        tw.write_text(page,color=(0,0,0),opacity=0.9)
        return True

    def save(self):
        # self.new_doc.ez_save(self.output_path)
        self.new_doc.save(self.output_path,garbage=4,clean=False,deflate=False)

if __name__ == "__main__":
    filename = './pdf/sec22arp.pdf'
    conf = config_load('./config.yml')
    if len(sys.argv) >= 2:
        filename = sys.argv[1]
    pe = PaperExtract(path=filename,font=conf['FONT'])
    pe.extract_textinfo()
    # print(pe.pages_info)
    print(pe.get_page_text(0))
    print(pe.get_page_text_array(0))
    # print(pe.get_all_text())
    pe.fill_all_blocks()
    pe.visualize_blocks()

    array=[]
    for i in range(pe.page_count):
        # print(len(pe.get_page_text_array(i)))
        array.append(len(pe.get_page_text_array(i)))
    print(array)
    # pe.insert_txt(11,txt)
    pe.save()

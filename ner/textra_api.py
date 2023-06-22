#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Sample Python3 OAuthRequest
"""

import sys
import requests as req
import json
import time
from requests_oauthlib import OAuth1
from xml.etree.ElementTree import *
from .yml_load import config_load
from .paper_extract import PaperExtract

priority = ['GOOGLE','TEXTRA']
class Textra_connection:
    def __init__(self,path,priority='GOOGLE') -> None:
        conf = config_load('./config.yml')
        self.priority = priority
        if 'PRIORITY' in conf:
            self.priority = conf['PRIORITY']
        if 'TEXTRA' in conf:
            self.NNAME = conf['TEXTRA']['NAME']
            self.NKEY = conf['TEXTRA']['KEY']
            self.NSECRET = conf['TEXTRA']['SECRET']
            self.NURL = conf['TEXTRA']['URL']
        if 'GOOGLE' in conf:
            self.GURL = conf['GOOGLE']['URL']
        if 'FONT' in conf:
            self.font = conf['FONT']
        self.pe = PaperExtract(path=path,font=self.font)

        self.consumer = OAuth1(self.NKEY , self.NSECRET)
        TEXT = ''
        self.params = {
            'key': self.NKEY,
            'name': self.NNAME,
            'type': 'json',
            'text': TEXT,
        }    # その他のパラメータについては、各APIのリクエストパラメータに従って設定してください。

    def get_translation_service_func(self):
        all = {
            'GOOGLE': self._post_block_google,
            'TEXTRA': self._post_block_nict,
        }
        return all

    def _post_block(self,txt,sleeptime=0.0):
        return self.get_translation_service_func()[self.priority](txt,sleeptime)
    
    def get_pdf_info(self):
        self.pe.extract_textinfo()
        self.pe.fill_all_blocks()

    def post_page(self):
        page_array = []
        for i in range(self.pe.page_count):
            page_array.append([])
            for txt in self.pe.get_page_text_array(i):
                print(i)
                try:
                    load = self._post_block(txt)
                    # page_array[i].append(load['resultset']['result']['text'])
                    page_array[i].append(load)

                except Exception as e:
                    print('=== Error ===')
                    print('type:' + str(type(e)))
                    print('args:' + str(e.args))
                    print('e:' + str(e))
                    try:
                        print('RE-POST:')
                        load = self._post_block(txt)
                        # page_array[i].append(load['resultset']['result']['text'])
                        page_array[i].append(load)
                    except Exception as ree:
                        print('=== Error ===')
                        print('Re:type:' + str(type(ree)))
                        print('Re:args:' + str(ree.args))
                        print('Re:e:' + str(ree))
                        page_array[i].append(txt)
                    # 再送したい
        self.page_array = page_array

    def _post_block_google(self,txt,sleeptime=3.0):
        res = ""
        time.sleep(sleeptime)
        # if (form == "google"):
        if True:
            params = {
                'client': 'gtx',
                'sl': 'en',
                'tl': 'ja',
                'dt': 't',
                'q': txt,
            }
            print("req>>" , txt)
            res = req.get(self.GURL, params=params)
            res.encoding = 'utf-8'
            load = res.json()
            # print("j")
            # print(type(j))
            txt = ''
            for k in range(len(load[0])):
                # print(j[0][k][0])
                txt += load[0][k][0]
            print(txt.replace('\n','').replace(' ',''))
            return txt.replace(' ','')

        return "cant translete"

    def _post_block_nict(self,txt,sleeptime=3.0) -> dict:
        self.params['text'] = txt
        print(self.params)
        time.sleep(sleeptime)
        res = req.post(self.NURL , data=self.params , auth=self.consumer)
        res.encoding = 'utf-8'
        # print("[res]")
        # print(res)
        # print('result')
        dump = json.dumps(res.text)
        load = json.loads(json.loads(dump))
        print(load['resultset']['request']['text'])
        print(load['resultset']['result']['text'])
        # return load
        return load['resultset']['result']['text'].replace(' ','')

    def insert(self):
        for i in range(self.pe.page_count):
            self.pe.insert_txt_from_array(i,self.page_array[i])
        
        self.pe.save()

if __name__ == "__main__":
    filename = './pdf/survey_watermark.pdf'
    if len(sys.argv) >= 2:
        filename = sys.argv[1]
    tc = Textra_connection(filename)
    tc.get_pdf_info()
    tc.post_page()
    tc.insert()


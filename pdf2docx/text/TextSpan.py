# -*- coding: utf-8 -*-

'''
Text Span object based on PDF raw dict extracted with PyMuPDF.

@created: 2020-07-22
@author: train8808@gmail.com
---

Refer to: https://pymupdf.readthedocs.io/en/latest/textpage.html

data structure for Span
    {
        # raw dict
        ---------------------------
        'bbox': (x0,y0,x1,y1),
        'color': sRGB
        'font': fontname,
        'size': fontzise,
        'flags': fontflags,
        'chars': [ chars ],

        # added dict
        ----------------------------
        'text': text,
        'style': [
            {
                'type': int,
                'color': int
            },
            ...
        ]
    }
'''


import fitz
import copy
from .Char import Char
from ..common.BBox import BBox
from ..common import utils
from ..shape.Rectangle import Rectangle


class TextSpan(BBox):
    '''Object representing text span.'''
    def __init__(self, raw: dict) -> None:
        super(TextSpan, self).__init__(raw)
        self.color = raw.get('color', 0)
        self.font = raw.get('font', None)
        self.size = raw.get('size', 12.0)
        self.flags = raw.get('flags', 0)
        self.chars = [ Char(c) for c in raw.get('chars', []) ]

        # introduced attributes
        self._text = None
        self.style = [] # a list of dict: { 'type': int, 'color': int }


    @property
    def text(self):
        '''Joining chars in text span'''
        if self._text is None:
            chars = [char.c for char in self.chars]
            self._text = ''.join(chars)
        
        return self._text

    def store(self) -> dict:
        res = super().store()
        res.update({
            'color': self.color,
            'font': self.font,
            'size': self.size,
            'flags': self.flags,
            'chars': [
                char.store() for char in self.chars
            ]
        })
        return res

    def plot(self, page, color:tuple):
        '''Fill bbox with given color.
           ---
            Args: 
              - page: fitz.Page object
        '''
        page.drawRect(self.bbox, color=color, fill=color, width=0, overlay=False)


    def split(self, rect:Rectangle) -> list:
        '''Split span with the intersection: span-intersection-span.'''
        # any intersection in this span?
        intsec = rect.bbox & self.bbox

        # no, then add this span as it is
        if not intsec: return [self]

        # yes, then split spans:
        # - add new style to the intersection part
        # - keep the original style for the rest
        split_spans = [] # type: list[TextSpan]

        # expand the intersection area, e.g. for strike through line,
        # the intersection is a `line`, i.e. a rectangle with very small height,
        # so expand the height direction to span height
        intsec.y0 = self.bbox.y0
        intsec.y1 = self.bbox.y1

        # calculate chars in the format rectangle
        # combine an index with enumerate(), so the second element is the char
        f = lambda items: items[1].contained_in_rect(rect)
        index_chars = list(filter(f, enumerate(self.chars)))

        # then we get target chars in a sequence
        pos = index_chars[0][0] if index_chars else -1 # start index -1 if nothing found
        length = len(index_chars)
        pos_end = max(pos+length, 0) # max() is used in case: pos=-1, length=0

        # split span with the intersection: span-intersection-span
        # 
        # left part if exists
        if pos > 0:
            split_span = copy.deepcopy(self)
            split_span.bbox = (self.bbox.x0, self.bbox.y0, intsec.x0, self.bbox.y1)
            split_span.chars = self.chars[0:pos]
            split_span.text = self.text[0:pos]
            split_spans.append(split_span)

        # middle intersection part if exists
        if length > 0:
            split_span = copy.deepcopy(self)            
            split_span.bbox = (intsec.x0, intsec.y0, intsec.x1, intsec.y1)
            split_span.chars = self.chars[pos:pos_end]
            split_span.text = self.text[pos:pos_end]

            # update style
            new_style = rect.to_text_style(split_span)
            if new_style:
                split_span.style.append(new_style)

            split_spans.append(split_span)                

        # right part if exists
        if pos_end < len(self.chars):
            split_span = copy.deepcopy(self)
            split_span.bbox = (intsec.x1, self.bbox.y0, self.bbox.x1, self.bbox.y1)
            split_span.chars = self.chars[pos_end:]
            split_span.text = self.text[pos_end:]
            split_spans.append(split_span)

        return split_spans


    def intersect(self, rect:fitz.Rect):
        '''Create new Span object with chars contained in given bbox. '''
        # add span directly if fully contained in bbox
        if rect.contains(self.bbox):
            return self.copy()

        # no intersection
        if not rect.intersects(self.bbox):
            return TextSpan()

        # furcher check chars in span
        span_chars = [] # type: list[Char]
        span_bbox = fitz.Rect()
        for char in self.chars:
            if utils.get_main_bbox(char.bbox, rect, 0.2):
                span_chars.append(char)
                span_bbox = span_bbox | self.bbox
        
        if not span_chars: return TextSpan()
            
        # update span
        span = self.copy()
        span.chars = span_chars
        span.update(span_bbox)

        return span
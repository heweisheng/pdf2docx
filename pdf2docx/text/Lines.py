# -*- coding: utf-8 -*-

'''
A group of Line objects.

@created: 2020-07-24
@author: train8808@gmail.com
'''

from docx.shared import Pt
from .Line import Line
from ..image.ImageSpan import ImageSpan
from ..common.Collection import Collection
from ..common.docx import add_stop
from ..common.share import TextAlignment
from ..common import constants


class Lines(Collection):
    '''Text line list.'''

    @property
    def unique_parent(self):
        '''Whether all contained lines have same parant.'''
        if not bool(self): return False

        first_line = self._instances[0]
        return all(line.same_parent_with(first_line) for line in self._instances)


    def append(self, line:Line):
        '''Override. Append a line and update line pid and parent bbox.'''
        super().append(line)

        # update original parent id
        if not self._parent is None:
            line.pid = id(self._parent)


    def restore(self, raws:list):
        '''Construct lines from raw dicts list.'''
        for raw in raws:
            line = Line(raw)
            self.append(line)
        return self


    @property
    def image_spans(self):
        '''Get all ImageSpan instances.'''
        spans = []
        for line in self._instances:
            spans.extend(line.image_spans)
        return spans

    
    def join(self, line_overlap_threshold:float, line_merging_threshold:float):
        ''' Merge lines aligned horizontally, e.g. make inline image as a span in text line.'''
        # skip if empty
        if not self._instances: return self
    
        # sort lines
        self.sort()

        # check each line
        lines = Lines()
        for line in self._instances:

            # first line
            if not lines: lines.append(line)
            
            # ignore this line if overlap with previous line
            elif line.get_main_bbox(lines[-1], threshold=line_overlap_threshold):
                print(f'Ignore Line "{line.text}" due to overlap')

            # add line directly if not aligned horizontally with previous line
            elif not line.in_same_row(lines[-1]):
                lines.append(line)

            # if it exists x-distance obviously to previous line,
            # take it as a separate line as it is
            elif abs(line.bbox.x0-lines[-1].bbox.x1) > line_merging_threshold:
                lines.append(line) 

            # now, this line will be append to previous line as a span
            else:
                lines[-1].add(list(line.spans))

        # update lines in block
        self.reset(lines)


    def split(self, threshold:float):
        ''' Split vertical lines and try to make lines in same original text block grouped together.

            To the first priority considering docx recreation, horizontally aligned lines must be assigned to same group.
            After that, if only one line in each group, lines in same original text block can be group together again 
            even though they are in different physical lines.
        '''
        # split vertically
        # set a non-zero but small factor to avoid just overlaping in same edge
        fun = lambda a,b: a.horizontally_align_with(b, factor=threshold)
        groups = self.group(fun) 

        # check count of lines in each group
        for group in groups:
            if len(group) > 1: # first priority
                break
        
        # now one line per group -> docx recreation is fullfilled, 
        # then consider lines in same original text block
        else:
            fun = lambda a,b: a.same_parent_with(b)
            groups = self.group(fun)

        # NOTE: group() may destroy the order of lines, so sort in line level
        for group in groups: group.sort()

        return groups


    def strip(self):
        '''remove redundant blanks of each line.'''
        # strip each line
        status = [line.strip() for line in self._instances]

        # update bbox
        stripped = any(status)
        if stripped: self._parent.update_bbox(self.bbox)

        return stripped


    def sort(self):
        ''' Sort lines considering text direction.
            Taking natural reading direction for example:
            reading order for rows, from left to right for lines in row.

            In the following example, A should come before B.
            ```
                             +-----------+
                +---------+  |           |
                |   A     |  |     B     |
                +---------+  +-----------+
            ```
            Steps:
              - sort lines in reading order, i.e. from top to bottom, from left to right.
              - group lines in row
              - sort lines in row: from left to right
        '''
        # sort in reading order
        self.sort_in_reading_order()

        # split lines in separate row
        lines_in_rows = [] # type: list[list[Line]]

        for line in self._instances:

            # add lines to a row group if not in same row with previous line
            if not lines_in_rows or not line.in_same_row(lines_in_rows[-1][-1]):
                lines_in_rows.append([line])
            
            # otherwise, append current row group
            else:
                lines_in_rows[-1].append(line)
        
        # sort lines in each row: consider text direction
        idx = 0 if self.is_horizontal_text else 3
        self._instances = []
        for row in lines_in_rows:
            row.sort(key=lambda line: line.bbox[idx])
            self._instances.extend(row)


    def group_by_columns(self):
        ''' Group lines into columns.'''
        # split in columns
        fun = lambda a,b: a.vertically_align_with(b, text_direction=False)
        groups = self.group(fun)
        
        # NOTE: increasing in x-direction is required!
        groups.sort(key=lambda group: group.bbox.x0)
        return groups


    def group_by_rows(self):
        ''' Group lines into rows.'''
        # split in rows, with original text block considered
        groups = self.split(threshold=0.0)

        # NOTE: increasing in y-direction is required!
        groups.sort(key=lambda group: group.bbox.y0)

        return groups


    def parse_text_format(self, rect):
        '''parse text format with style represented by rectangle shape.
            ---
            Args:
            - rect: potential style shape applied on blocks
        '''
        flag = False

        for line in self._instances:
            # any intersection in this line?
            intsec = rect.bbox & line.get_expand_bbox(constants.MAJOR_DIST)
            
            if not intsec: 
                if rect.bbox.y1 < line.bbox.y0: break # lines must be sorted in advance
                continue

            # yes, then try to split the spans in this line
            split_spans = []
            for span in line.spans: 
                # include image span directly
                if isinstance(span, ImageSpan): split_spans.append(span)                   

                # split text span with the format rectangle: span-intersection-span
                else:
                    spans = span.split(rect, line.is_horizontal_text)
                    split_spans.extend(spans)
                    flag = True
                                            
            # update line spans                
            line.spans.reset(split_spans)

        return flag


    def parse_line_break(self, line_free_space_ratio_threshold):
        ''' Whether hard break each line.

            Hard line break helps ensure paragraph structure, but pdf-based layout calculation may
            change in docx due to different rendering mechanism like font, spacing. For instance, when
            one paragraph row can't accommodate a Line, the hard break leads to an unnecessary empty row.
            Since we can't 100% ensure a same structure, it's better to focus on the content - add line
            break only when it's necessary to, e.g. explicit free space exists.            
        '''
        block = self.parent        
        idx0 = 0 if block.is_horizontal_text else 3
        idx1 = (idx0+2)%4 # H: x1->2, or V: y0->1
        width = abs(block.bbox[idx1]-block.bbox[idx0])

        # space for checking line break
        if block.alignment == TextAlignment.RIGHT:
            delta_space = block.left_space_total - block.left_space
            idx = idx0
        else:
            delta_space = block.right_space_total - block.right_space
            idx = idx1        

        for i, line in enumerate(self._instances):            
            if line==self._instances[-1]: # no more lines after last line
                line.line_break = 0
            
            elif line.in_same_row(self._instances[i+1]):
                line.line_break = 0
            
            # break line if free space exceeds a threshold
            elif (abs(block.bbox[idx]-line.bbox[idx]) + delta_space) / width > line_free_space_ratio_threshold:
                line.line_break = 1
            
            # break line if next line is a only a space (otherwise, MS Word leaves it in previous line)
            elif not self._instances[i+1].text.strip():
                line.line_break = 1
            
            else:
                line.line_break = 0


    def make_docx(self, p):
        '''Create lines in paragraph.'''
        block = self.parent        
        idx0 = 0 if block.is_horizontal_text else 3
        idx1 = (idx0+2)%4 # H: x1->2, or V: y0->1
        current_pos = block.left_space

        for i, line in enumerate(self._instances):
            # left indentation implemented with tab
            pos = block.left_space + (line.bbox[idx0]-block.bbox[idx0])
            if pos>block.left_space and block.tab_stops: # sometimes set by first line indentation
                add_stop(p, Pt(pos), Pt(current_pos))

            # add line
            line.make_docx(p)

            # update stop position            
            if line==self._instances[-1]: break
            if line.in_same_row(self._instances[i+1]):
                current_pos = pos + abs(line.bbox[idx1]-block.bbox[idx0])
            else:
                current_pos = block.left_space
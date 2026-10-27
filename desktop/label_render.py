import math
import qrcode
from PySide6.QtCore import Qt,QRectF
from PySide6.QtGui import QImage,QPainter,QFont,QColor,QPen
from label_contract import validate_template,bind,ContractError,decode_logo_png

def printable_logo(encoded,crop_white=False):
    """Render the original embedded asset as solid ink on white for ribbon printers."""
    logo=QImage.fromData(decode_logo_png(encoded),'PNG')
    if logo.isNull():raise ContractError('Logo PNG 无法解码')
    flat=QImage(logo.size(),QImage.Format.Format_RGB32);flat.fill(Qt.GlobalColor.white)
    painter=QPainter(flat)
    try:painter.drawImage(0,0,logo)
    finally:painter.end()
    gray=flat.convertToFormat(QImage.Format.Format_Grayscale8);data=bytes(gray.constBits());stride=gray.bytesPerLine()
    for y in range(flat.height()):
        for x,value in enumerate(data[y*stride:y*stride+flat.width()]):flat.setPixel(x,y,0xff000000 if value<245 else 0xffffffff)
    if crop_white:
        xmin,ymin=flat.width(),flat.height();xmax,ymax=-1,-1
        for y in range(flat.height()):
            for x,value in enumerate(data[y*stride:y*stride+flat.width()]):
                if value<128:xmin=min(xmin,x);xmax=max(xmax,x);ymin=min(ymin,y);ymax=max(ymax,y)
        if xmax>=xmin:flat=flat.copy(xmin,ymin,xmax-xmin+1,ymax-ymin+1)
    return flat

def translate_label(image,x_mm,y_mm,dpi):
    from workstation import CalibrationStore
    CalibrationStore.validate(x_mm,y_mm)
    dx,dy=round(x_mm*dpi/25.4),round(y_mm*dpi/25.4)
    if not dx and not dy:return image.copy()
    gray=image.convertToFormat(QImage.Format.Format_Grayscale8);raw=bytes(gray.constBits());stride=gray.bytesPerLine()
    for y in range(image.height()):
        for x,value in enumerate(raw[y*stride:y*stride+image.width()]):
            if value<128 and not (0<=x+dx<image.width() and 0<=y+dy<image.height()):
                raise ContractError('微调后有内容超出标签边界，请减小偏移或增加模板留白')
    result=QImage(image.size(),QImage.Format.Format_RGB32);result.fill(Qt.GlobalColor.white)
    result.setDotsPerMeterX(image.dotsPerMeterX());result.setDotsPerMeterY(image.dotsPerMeterY())
    painter=QPainter(result)
    try:painter.drawImage(dx,dy,image)
    finally:painter.end()
    return result

def render_label(template,values,dpi=203):
    validate_template(template)
    if dpi not in (203,300,600):raise ContractError('不支持的 DPI')
    ratio=dpi/25.4
    width=round(template['width']*ratio);height=round(template['height']*ratio)
    image=QImage(width,height,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.white)
    image.setDotsPerMeterX(round(dpi/.0254));image.setDotsPerMeterY(round(dpi/.0254))
    p=QPainter(image);warnings=[]
    try:
        for e in template['elements']:
            x,y,w,h=[e[k]*ratio for k in ('x','y','w','h')]
            value=bind(e.get('content',''),values)
            if '{{' in value:raise ContractError('存在未绑定模板字段')
            p.save();p.setClipRect(QRectF(x,y,w,h));p.setPen(Qt.GlobalColor.black)
            if e['type']=='text':
                font=QFont('Arial');font.setPixelSize(max(1,round(e['font']*ratio)));font.setBold(bool(e.get('bold')))
                p.setFont(font);p.setRenderHint(QPainter.RenderHint.TextAntialiasing,False)
                lines=value.split('\n');metrics=p.fontMetrics()
                if any(metrics.horizontalAdvance(s)>w+.5 for s in lines) or e['font']*ratio*1.25*len(lines)>h+.5:warnings.append((e.get('name') or '文本')+'可能被裁切')
                alignment={'left':Qt.AlignmentFlag.AlignLeft,'center':Qt.AlignmentFlag.AlignHCenter,'right':Qt.AlignmentFlag.AlignRight}[e.get('align','left')]
                for i,line in enumerate(lines):p.drawText(QRectF(x,y+i*e['font']*ratio*1.25,w,h),alignment|Qt.AlignmentFlag.AlignTop,line)
            elif e['type']=='qr':
                if not value:raise ContractError('二维码内容不能为空')
                q=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,border=4,box_size=1);q.add_data(value.encode('utf-8'),optimize=0);q.make(fit=True);matrix=q.get_matrix()
                module=math.floor(min(w,h)/len(matrix))
                if module<2:raise ContractError('二维码过密，每模块不足 2 点，请放大标签中的二维码')
                if module<3:warnings.append('二维码每模块不足 3 点，需放大或实机扫码验证')
                ox=round(x+(w-module*len(matrix))/2);oy=round(y+(h-module*len(matrix))/2)
                p.fillRect(QRectF(x,y,w,h),Qt.GlobalColor.white)
                for row,line in enumerate(matrix):
                    for col,dark in enumerate(line):
                        if dark:p.fillRect(ox+col*module,oy+row*module,module,module,Qt.GlobalColor.black)
            elif e['type']=='line':p.fillRect(QRectF(x,y,w,max(1,h)),Qt.GlobalColor.black)
            elif e['type']=='image':
                logo=printable_logo(e['image_png'],e.get('crop_white',False));scale=min(w/logo.width(),h/logo.height())
                lw,lh=logo.width()*scale,logo.height()*scale
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,False)
                p.drawImage(QRectF(x+(w-lw)/2,y+(h-lh)/2,lw,lh),logo)
            elif e['type']=='rect':
                p.setPen(QPen(QColor('black'),max(1,.2*ratio)));p.drawRect(QRectF(x+.1*ratio,y+.1*ratio,w-.2*ratio,h-.2*ratio))
            p.restore()
    finally:p.end()
    return image,list(dict.fromkeys(warnings))

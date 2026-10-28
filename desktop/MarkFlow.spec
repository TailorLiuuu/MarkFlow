from pathlib import Path
import sys
root=Path(SPECPATH)
a=Analysis([str(root/'app.py')],pathex=[str(root)],binaries=([(str(root/'sdk/LP_LabelPrinter.dll'),'sdk')] if sys.platform=='win32' and (root/'sdk/LP_LabelPrinter.dll').exists() else []),datas=[(str(root/'examples'),'examples'),(str(root/'assets'),'assets')],hiddenimports=['PySide6.QtPrintSupport','esptool','esptool.targets.esp32s3'],hookspath=[],hooksconfig={},runtime_hooks=[],excludes=[],noarchive=False)
pyz=PYZ(a.pure)
app=EXE(pyz,a.scripts,[],exclude_binaries=True,name='MarkFlow',icon=str(root/'assets/markflow.ico'),debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False)
worker=EXE(pyz,a.scripts,[],exclude_binaries=True,name='LabelDeskWorker',debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=True)
u=Analysis([str(root/'updater_worker.py')],pathex=[str(root)],binaries=[],datas=[],hiddenimports=[],hookspath=[],hooksconfig={},runtime_hooks=[],excludes=[],noarchive=False)
updater=EXE(PYZ(u.pure),u.scripts,u.binaries,u.datas,[],name='MarkFlowUpdater',debug=False,strip=False,upx=False,console=False)
coll=COLLECT(app,worker,updater,a.binaries,a.datas,strip=False,upx=False,name='MarkFlow')

if sys.platform=='darwin':
    app_bundle=BUNDLE(coll,name='MarkFlow.app',bundle_identifier='cn.infinlab.labeldesk',info_plist={'NSHighResolutionCapable':True,'CFBundleShortVersionString':'0.4.5','NSHumanReadableCopyright':'InfinLab MarkFlow'})

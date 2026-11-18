import sys, cv2, numpy as np
files=sys.argv[2:]; ims=[cv2.imread(f) for f in files]
H=max(i.shape[0] for i in ims); W=max(i.shape[1] for i in ims)
cols=4; rows=-(-len(ims)//cols)
g=np.full((rows*H,cols*W,3),255,np.uint8)
for k,i in enumerate(ims):
    r,c=divmod(k,cols); g[r*H:r*H+i.shape[0],c*W:c*W+i.shape[1]]=i
    cv2.putText(g,files[k].split('/')[-1][:-4],(c*W+2,r*H+12),0,0.4,(0,160,0),1)
s=min(1.0,1600/g.shape[1]); g=cv2.resize(g,None,fx=s,fy=s,interpolation=cv2.INTER_AREA)
cv2.imwrite(sys.argv[1],g)

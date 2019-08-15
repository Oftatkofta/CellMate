import numpy as np
import tifffile.tifffile as tifffile
import re
import cv2 as cv
import time
import os
import pandas as pd


class Channel(object):
    """
    Class to keep track of one channel from a Micromanager OME-TIFF. Only handles a single slice.
    Acts as a shallow copy of the channels TiffPages from a TiffFile until a nupy array is generated.
    Stores the Numpy array, median projection, and analysis
    """
    def __init__(self, chIndex, tiffFile, name, sliceIndex=0):
        """
        :param chIndex: (int) index of channel to create
        :param tiffFile: (TiffFile) to extract channel from
        :param name: (str) name of channel
        :param slice: (int) slice to extract, defaults to 0
        """
        self.chIndex = chIndex
        self.sliceIdx = sliceIndex
        self.tif = tiffFile
        self.name = name
        self.tif_mm_metadata = tiffFile.micromanager_metadata
        self.pxSize_um, self.finterval_ms = self._read_px_size_and_finteval() #finterval from settings, not actual
        self.elapsedTimes_ms = [] #_page_extractor method populates this
        self.pages = self._page_extractor()
        self.array = None # getArray populates this when called
        self.actualFrameIntervals_ms = None #getActualFrameIntervals_ms populates this when called
        self.medianArray = None # getTemporalMedianFilterArray populates when called
        self.frameSamplingInterval = None # getTemporalMedianFilterArray populates when called


    def _page_extractor(self):
        """
        :return: (list) Tif-Page objects corresponding to the chosen slice and channel
        """

        out = []
        sliceMap = self.tif_mm_metadata["IndexMap"]["Slice"]
        channelMap = self.tif_mm_metadata["IndexMap"]["Channel"]

        for i in range(len(self.tif.pages)):
            if (sliceMap[i] == self.sliceIdx) and (channelMap[i] == self.chIndex):
                page = self.tif.pages[i]
                out.append(page)
                self.elapsedTimes_ms.append(page.tags["MicroManagerMetadata"].value["ElapsedTime-ms"])

        return out

    def _read_px_size_and_finteval(self):
        """
        Determines which version of MM that was used to acquire the data.
        versions 1.4 and 2.0-gamma, share Metadata structure, but 2.0.0-beta is slightly different
        in where the frame interval and pixel sizes can be read from. In 2.0-beta the
        frame interval is read from tif.micromanager_metadata['Summary']['WaitInterval'],
        and in 1.4/2.0-gamma it is read from tif.micromanager_metadata['Summary']['Interval_ms']

        Pixel size is read from tif.micromanager_metadata['Summary']['PixelSize_um'] in 1.4/2.0-gamma, but from
        tif.micromanager_metadata['PixelSize_um'] in 2.0-beta

        MM versions used for testing>
        MicroManagerVersion 1.4.23 20180220
        MicroManagerVersion 2.0.0-gamma1 20190527
        MicroManagerVersion 2.0.0-beta3 20180923

            :param mm_metadata:
            (dict) MicroManager metadata dictionary

            :return:
            (tuple) (pixel_size_um, frame_interval)
        """
        one4_regex = re.compile("1\.4\.[\d]")  # matches 1.4.d
        gamma_regex = re.compile("gamma")
        beta_regex = re.compile("beta")

        version = self.tif_mm_metadata["Summary"]["MicroManagerVersion"]

        if (re.search(beta_regex, version) != None):
            finterval_ms = self.tif_mm_metadata['Summary']['WaitInterval']
            px_size_um = self.tif_mm_metadata['PixelSize_um']

            return px_size_um, finterval_ms

        elif (re.search(one4_regex, version) != None):
            finterval_ms = self.tif_mm_metadata['Summary']['Interval_ms']
            px_size_um = self.tif_mm_metadata['PixelSizeUm']

            return px_size_um, finterval_ms

        elif (re.search(gamma_regex, version) != None):
            finterval_ms = self.tif_mm_metadata['Summary']['Interval_ms']
            px_size_um = self.tif_mm_metadata['PixelSizeUm']

            return px_size_um, finterval_ms

    def getPages(self):

        return self.pages

    def getElapsedTimes_ms(self):

        return self.elapsedTimes_ms

    def getArray(self):

        if (self.array != None):

            return self.array

        else:
            outshape = (len(self.pages),
                        self.tif_mm_metadata['Summary']["Width"],
                        self.tif_mm_metadata['Summary']["Height"])

            outType = self.pages[0].asarray().dtype

        out = np.empty(outshape, outType)

        for i in range(len(self.pages)):
            out[i] = self.pages[i].asarray()

        return out

    def getTemporalMedianFilterArray(self, startFrame=0, stopFrame=None,
                               frameSamplingInterval=3, recalculate=False):
        """
        The function first runs a gliding N-frame temporal median on every pixel to
        smooth out noise and to remove fast moving debris that is not migrating
        cells. Recalculates the median array if recalculate is True.

        :param arr:
            (3d numpy array) with a shape of (t, y, x)
        :param stopFrame:
            (int) Last frame to analyze, defalults to analyzing all frames if None
        :param startFrame:
            (int) First frame to analyze
        :param frameSamplingInterval:
            (int) do median projection every n frames
        :param recalculate:
            (bool) Should the median projection be recalculated?

        :return:
            An Nupy array of the type float32

        """
        try:
            if (self.medianArray != None) and not recalculate:

                return self.medianArray
        except ValueError: #np arrays are ambigous if checked for None

            if not recalculate:

                return self.medianArray

        if (stopFrame == None) or (stopFrame > len(self.pages)):
            stopFrame = len(self.pages)

        if (startFrame >= stopFrame):
            raise ValueError("StartFrame cannot be larger than Stopframe!")

        if (stopFrame-startFrame < frameSamplingInterval):
            raise ValueError("Not enough frames selected to do median projection! ")


        self.frameSamplingInterval = frameSamplingInterval
        arr = self.getArray()
        # nr_out_frames = n_in-(samplingInterval-1), stopFrame is an index so 1 has to be added to get the nr of frames
        nr_outframes = (stopFrame - startFrame) - (frameSamplingInterval - 1)

        outshape = (nr_outframes, arr.shape[1], arr.shape[2])

        self.medianArray = np.ndarray(outshape, dtype=np.float32)
        fr = np.ndarray((arr.shape[1], arr.shape[2]), dtype=np.float32)

        outframe = 0

        for inframe in range(startFrame, stopFrame-frameSamplingInterval+1):

            # median of frames n1,n2,n3...
            self.medianArray[outframe] = np.median(arr[inframe:inframe + frameSamplingInterval], axis=0, out=fr)
            outframe += 1

        return self.medianArray


    def getActualFrameIntevals_ms(self):
        # the intervals between frames in ms as a 1D numpy array
        # returns None if only one frame exists

        if (self.actualFrameIntervals_ms != None):

            return self.actualFrameIntervals_ms

        elif len(self.pages) == 1:

            return None

        else:
            out = []
            t0 = self.elapsedTimes_ms[0]
            for t in self.elapsedTimes_ms[1:]:
                out.append(t-t0)
                t0 = t
            return np.asarray(out)

    def getIntendedFrameInterval_ms(self):

        return self.finterval_ms

    def doFrameIntervalSanityCheck(self, maxDiff=0.01):
        #Checks if the intended frame interval matches the actual within maxDiff

        if len(self.pages) == 1:
            return None

        else:
            fract = self.getActualFrameIntevals_ms().mean()/self.getIntendedFrameInterval_ms()
            out = abs(1-fract) < maxDiff

            return out

    def rehapeMedianFramesTo6d(self):
        #reshapes 3D (t, x, y) array to (t, 1, 1, x, y, 1) for saving dimensions in TZCYXS order
        shape = self.medianArray.shape
        self.medianArray.shape = (shape[0], 1, 1, shape[1], shape[2], 1)

class Analyzer(object):

    def __init__(self, channel):

        self.channel = channel
        self.progress = 0 #0-100 for pyQt5 progressbar

class FarenbackAnalyzer(Analyzer):

    def __init__(self, channel):
        super().__init__(channel)

        self.flows = None # (t, x, y, uv) numpy array
        self.speeds = None # (t ,x, y) 3D numpy-array
        self.avg_speeds = None # 1D numpy array of frame average speeds
        self.histograms = None # populated by doFlowsToAvgSpeed if the doHist flag True
        self.drawnFrames = None
        self.scaler = self._getScaler() #value to multiply vector lengths by to get um/min from px/frame

    def _getScaler(self):
        """
        Calculates constant to scale px/frame to um/min in the unit um*frame/px*min

        um/px * frames/min * px/frame = um/min

        :return: (float) scaler
        """
        if not self.channel.doFrameIntervalSanityCheck():
            print("Replacing intended interval with actual!")
            finterval_ms = self.channel.getActualFrameIntevals_ms().mean()
            finterval_s = round(finterval_ms / 1000, 2)
            frames_per_min = round(60 / finterval_s, 2)
            tunit = 's'
            self.channel.scaler = self.channel.pxSize_um * frames_per_min  # um/px * frames/min * px/frame = um/min
            Ch0.finterval_ms = finterval_ms

        finterval_s = self.channel.finterval_ms / 1000
        frames_per_min = finterval_s / 60

        return self.channel.pxSize_um * frames_per_min



    def doFarenbackFlow(self, pyr_scale=0.5, levels=3, winsize=15, iterations=3, poly_n=5, poly_sigma=1.2, flags=0):
        """
        Calculates Farenback flow for a single channel time lapse

        returns numpy array of dtype int32 with flow in px/frame
        """

        arr = self.channel.getTemporalMedianFilterArray()
        self.flows = np.empty((arr.shape[0] - 1, arr.shape[1], arr.shape[2], 2), dtype=np.float32)

        for i in range(arr.shape[0] - 1):

            flow = cv.calcOpticalFlowFarneback(arr[i],
                                               arr[i + 1],
                                               None,
                                               pyr_scale,
                                               levels,
                                               winsize,
                                               iterations,
                                               poly_n,
                                               poly_sigma,
                                               flags)

            self.flows[i] = flow.astype(np.float32)
            self.progress = 100*i/(arr.shape[0] - 1)
            print("Progress: {:.2f} % on {}".format(self.progress, self.channel.name))

        return self.flows

    def doFlowsToSpeed(self, scaler=None, doAvgSpeed = False, doHist=False, nbins=10, hist_range=None):
        """
        Turns a (t, x, y, uv) flow numpy array with u/v component vectors in to a (t, x, y) speed array
        populates self.speeds.
        If doAvgSpeed is True the 1D array self.avg_speeds is also populated.
        If doHist is True the tuple self.histograms is populated (histograms, bins) histograms are calculated with
        nbins bins. hist_range defaults to (0, max_speed) if None

        Scales all the output by multiplying with scaler, defalut output is in um/min if scaler is None

        :returns self.speeds
        """
        if (scaler == None):
            scaler = self.scaler

        try:
            if self.flows == None:
                raise Exception("No flow calculated, please calculate flow first!")

        except ValueError:
            pass

        out = np.square(self.flows)
        out = out.sum(axis=3)
        out = np.sqrt(out) * scaler
        self.speeds = out

        if doHist:

            if (hist_range == None):
                hist_range = (0, out.max())

            print("Histogram range: {}".format(hist_range))
            hists = np.ones((self.flows.shape[0], nbins), dtype=np.float32)

            for i in range(self.flows.shape[0]):
                hist = np.histogram(out[i], bins=nbins, range=hist_range, density=True)
                hists[i] = hist[0]

            bins = hist[1]

            self.histograms = (hists, bins)

        if doAvgSpeed:

            self.avg_speeds = out.mean(axis=(1, 2))

        return self.speeds


    def _draw_flow_frame(self, img, flow, step=15, scale=20, line_thicknes=2):
        h, w = img.shape[:2]
        y, x = np.mgrid[step / 2:h:step, step / 2:w:step].reshape(2, -1).astype(int)
        fx, fy = flow[y, x].T * scale
        lines = np.vstack([x, y, x + fx, y + fy]).T.reshape(-1, 2, 2)
        lines = np.int32(lines + 0.5)
        vis = img.copy()
        cv.polylines(vis, lines, 0, 255, line_thicknes)
        #for (x1, y1), (_x2, _y2) in lines:
            # radius = int(math.sqrt((x1-_x2)**2+(y1-_y2)**2))
            #cv.circle(vis, (x1, y1), 1, 255, 1)

        return vis

    def _draw_scalebar(self, img, pxlength):
        """
        Draws a white scale bar in the bottom right corner

        :param img: 2D 8-bit np array to draw on
        :param pxlength: (int) length of scale bar in pixels
        :return: 2D 8-bit np array image with scale bar drawn on
        """

        h, w = img.shape[:2]
        from_x = w-32
        from_y = h-50
        to_x = from_x-pxlength
        to_y = from_y
        vis = img.copy()
        cv.line(vis, (from_x, from_y), (to_x, to_y), 255, 5)

        return vis

    def draw_all_flow_frames(self, scalebarFlag = False, scalebarLength=10, **kwargs):
        """
        Draws the flow on all the frames in bg with standard settings
        """
        flows = self.flows
        bg = self.channel.getTemporalMedianFilterArray()
        outshape = (flows.shape[0], flows.shape[1], flows.shape[2])
        out = np.empty(outshape, dtype='uint8')
        scale = kwargs["scale"]
        scalebar_px = int(scale*scalebarLength/self.scaler)

        if bg.dtype != np.dtype('uint8'):

            bg = normalization_to_8bit(bg)

        for i in range(out.shape[0]):

            out[i] = self._draw_flow_frame(bg[i], flows[i], **kwargs)
            if scalebarFlag:
                out[i] = self._draw_scalebar(out[i], scalebar_px)

        self.drawnFrames = out

        return out

    def rehapeDrawnFramesTo6d(self):
        #reshapes 3D (t, x, y) array to (t, 1, 1, x, y, 1) for saving dimensions in TZCYXS order

        if (len(self.drawnFrames.shape)==6):
            return None

        shape = self.drawnFrames.shape
        self.drawnFrames.shape = (shape[0], 1, 1, shape[1], shape[2], 1)

    def saveSpeedArray(self, outdir, fname=None):
        #Saves the speeds as a 32-bit tif
        shape = self.speeds.shape
        self.speeds.shape = (shape[0], 1, 1, shape[1], shape[2], 1) # dimensions in TZCYXS order

        if fname == None:
            saveme = os.path.join(outdir, self.channel.name + "_speeds.tif")

        else:
            saveme = os.path.join(outdir, fname)

        ij_metadatasave = {'unit': 'um', 'finterval': round(self.channel.finterval_ms/1000,2),
                           'tunit': "s", 'frames': shape[0],
                           'slices': 1, 'channels': 1}

        tifffile.imwrite(saveme, self.speeds.astype(np.float32),
                        imagej=True, resolution=(1 / self.channel.pxSize_um, 1 / self.channel.pxSize_um),
                        metadata=ij_metadatasave
                        )

    def saveSpeedCSV(self, outdir):
        #print("Saving csv of mean speeds...")
        if (len(self.speeds.shape) == 6):
            arr = np.average(self.speeds, axis=(3, 4))

        else:
            arr = np.average(self.speeds, axis=(1, 2))

        fr_interval = self.channel.frameSamplingInterval
        arr.shape = arr.shape[0] # make 1D

        timepoints_abs = np.arange(fr_interval-1, arr.shape[0] + fr_interval-1, dtype='float32') * self.channel.finterval_ms/1000

        df = pd.DataFrame(arr, index=timepoints_abs, columns=["AVG_frame_flow_um_per_min"])
        df.index.name = "Time(s)"
        saveme = os.path.join(outdir, self.channel.name + "_speeds.csv")
        df.to_csv(saveme)


def normalization_to_8bit(image_stack, lowPcClip = 0.175, highPcClip = 0.175):


    #clip image to saturate 0.35% of pixels 0.175% in each end by default.
    low = int(np.percentile(image_stack, lowPcClip))
    high = int(np.percentile(image_stack, 100 - highPcClip))

    # use linear interpolation to find new pixel values
    image_equalized = np.interp(image_stack.flatten(), (low, high), (0, 255))

    return image_equalized.reshape(image_stack.shape).astype('uint8')


def analyzeFiles(fnamelist, outdir, flowkwargs, scalebarFlag, scalebarLength):
    """
    Automatically analyzes tifffiles annd saves ouput in outfolder. If input has two channels, analysis is run on
    channel index 1
    :param tif: Tifffile objekt
    :return:
    """
    for fname in fnamelist:

        with tifffile.TiffFile(fname, multifile=False) as tif:
            lab = os.path.split(fname)[1][:-8]
            print("Working on: {} as {}".format(fname, lab))
            t1 = time.time()
            ij_metadata = tif.imagej_metadata
            n_channels = int(tif.micromanager_metadata['Summary']['Channels'])

            Ch0 = Channel(0, tif, name=lab + "_Ch1")
            print("Elapsed for file load and Channel creation {:.2f} s.".format(time.time() - t1))
            finterval_s = round(Ch0.finterval_ms / 1000, 2)
            frames_per_min = round(60 / finterval_s, 2)
            tunit = 's'
            print(
                "Intended dimensions: frame interval {:.2f}s, {:.2f} frames/min, pixel size: {:.2f} um ".format(finterval_s, frames_per_min, Ch0.pxSize_um))

            print("Actual frame interval is: {:.2f} s".format(Ch0.getActualFrameIntevals_ms().mean()/1000))
            if not Ch0.doFrameIntervalSanityCheck():
                print("Replacing intended interval with actual!")
                finterval_ms = Ch0.getActualFrameIntevals_ms().mean()
                finterval_s = round(finterval_ms / 1000, 2)
                frames_per_min = round(60 / finterval_s, 2)
                tunit = 's'
                Ch0.scaler = Ch0.pxSize_um * frames_per_min  # um/px * frames/min * px/frame = um/min
                Ch0.finterval_ms = finterval_ms

                print(
                    "Using dimensions: frame interval {:.2f}s, {:.2f} frames/min, pixel size: {:.2f} um ".format(
                        finterval_s, frames_per_min, Ch0.pxSize_um))

            print("Start median filter of Channel 1...")
            Ch0.getTemporalMedianFilterArray()
            print("Elapsed for file {:.2f} s, now calculating Channel 1 flow...".format(time.time() - t1))
            Analysis_Ch0 = FarenbackAnalyzer(Ch0)

            Analysis_Ch0.doFarenbackFlow()

            print("flow finished, calculating speeds...")
            Analysis_Ch0.doFlowsToSpeed()
            print("Saving speeds...as {}_speeds.tif".format(Analysis_Ch0.channel.name))
            Analysis_Ch0.saveSpeedArray(outdir)
            Analysis_Ch0.saveSpeedCSV(outdir)
            print("Elapsed for file {:.2f} s, now drawing flow...".format(time.time() - t1))
            Analysis_Ch0.draw_all_flow_frames(scalebarFlag, scalebarLength, **flowkwargs)

            Analysis_Ch0.rehapeDrawnFramesTo6d()

            ij_metadatasave = {'unit': 'um', 'finterval': finterval_s,
                               'tunit': tunit, 'Info': ij_metadata['Info'], 'frames': Analysis_Ch0.flows.shape[0],
                               'slices': 1, 'channels': n_channels}

            if n_channels == 2:
                print("Loading Channel 2")
                Ch1 = Channel(1, tif, name=lab + "_Ch2")
                print("Start median filter of Channel 2...")
                Ch1.getTemporalMedianFilterArray()
                Ch1.medianArray = normalization_to_8bit(Ch1.medianArray, lowPcClip=10, highPcClip=0)
                Ch1.rehapeMedianFramesTo6d()

                savename = os.path.join(outdir, lab + "_2Chan_flow.tif")
                #print(Analysis_Ch0.drawnFrames.shape, Ch1.medianArray[:stopframe-3].shape)
                arr_to_save = np.concatenate((Analysis_Ch0.drawnFrames, Ch1.medianArray[:-1]), axis=2)



            else:
                savename = os.path.join(outdir, lab + "_flow.tif")

                arr_to_save = Analysis_Ch0.drawnFrames

            print("Saving flow...")
            tifffile.imwrite(savename, arr_to_save.astype(np.uint8),
                                 imagej=True, resolution=(1 / Ch0.pxSize_um, 1 / Ch0.pxSize_um),
                                 metadata=ij_metadatasave
                                 )
            print("File done!")

    return True


if __name__ == '__main__':
    gamma = r"C:\Users\Jens\Microscopy\MMgamma_demodata\dummydata_2\dummydata_2_MMStack_Position_a.ome.tif"
    beta = r"C:\Users\Jens\Microscopy\MMgamma_demodata\dummydata_beta_1\dummydata_beta_1_MMStack_Pos0.ome.tif"
    onefour = r"C:\Users\Jens\Microscopy\MMgamma_demodata\dummy1_4_1\dummy1_4_1_MMStack_Pos0.ome.tif"
    fs_beta = r"C:\Users\Jens\Microscopy\FrankenScope2\_Pilar\Multi channel\plate3_Mss109_to15min_every20sec_1_MMStack_A_b.ome.tif"
    fs_beta2 = r"C:\Users\Jens\Microscopy\FrankenScope2\_Pilar\Multi channel\raw_STm infection_10x__1_MMStack_MOI2.ome.tif"
    fs_onefour = r"C:\Users\Jens\Downloads\SG_Mitotracker-green_Lysotracker-red_cellROX-deepRed_post_1_MMStack_3-Pos_001_001.ome.tif"

    # filelist = [gamma, beta, onefour, fs_beta]
    filelist = [fs_beta, fs_beta2]

    outdir = r"C:\Users\Jens\Microscopy\FrankenScope2\_Pilar\Multi channel"

    flowkwargs = {"step": 15, "scale": 20, "line_thicknes": 2}
    scalebarFlag = True
    scalebarLength = 1

    analyzeFiles(filelist, outdir, flowkwargs, scalebarFlag, scalebarLength)
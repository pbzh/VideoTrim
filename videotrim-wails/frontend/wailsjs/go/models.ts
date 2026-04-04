export namespace main {
	
	export class EncoderInfo {
	    label: string;
	    encoder: string;
	    hint: string;
	
	    static createFrom(source: any = {}) {
	        return new EncoderInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.label = source["label"];
	        this.encoder = source["encoder"];
	        this.hint = source["hint"];
	    }
	}
	export class TrimParams {
	    inputPath: string;
	    outputPath: string;
	    startTime: string;
	    endTime: string;
	    encoderMode: string;
	
	    static createFrom(source: any = {}) {
	        return new TrimParams(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.inputPath = source["inputPath"];
	        this.outputPath = source["outputPath"];
	        this.startTime = source["startTime"];
	        this.endTime = source["endTime"];
	        this.encoderMode = source["encoderMode"];
	    }
	}
	export class TrimResult {
	    success: boolean;
	    message: string;
	    fileSizeMB?: number;
	
	    static createFrom(source: any = {}) {
	        return new TrimResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.success = source["success"];
	        this.message = source["message"];
	        this.fileSizeMB = source["fileSizeMB"];
	    }
	}
	export class VideoInfo {
	    formatName: string;
	    videoCodec: string;
	    audioCodec: string;
	    durationMs: number;
	    fps: number;
	    error?: string;
	
	    static createFrom(source: any = {}) {
	        return new VideoInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.formatName = source["formatName"];
	        this.videoCodec = source["videoCodec"];
	        this.audioCodec = source["audioCodec"];
	        this.durationMs = source["durationMs"];
	        this.fps = source["fps"];
	        this.error = source["error"];
	    }
	}

}


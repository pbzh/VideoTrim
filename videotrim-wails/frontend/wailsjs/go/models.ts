export namespace main {
	
	export class BatchItem {
	    path: string;
	    startTime: string;
	
	    static createFrom(source: any = {}) {
	        return new BatchItem(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.path = source["path"];
	        this.startTime = source["startTime"];
	    }
	}
	export class BatchTrimResult {
	    total: number;
	    succeeded: number;
	    failed: number;
	    errors?: string[];
	
	    static createFrom(source: any = {}) {
	        return new BatchTrimResult(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.total = source["total"];
	        this.succeeded = source["succeeded"];
	        this.failed = source["failed"];
	        this.errors = source["errors"];
	    }
	}
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
	export class ScanRow {
	    file: string;
	    path: string;
	    frozenIntro: boolean;
	    firstChange: string;
	    freezeSec: number;
	    error?: string;
	
	    static createFrom(source: any = {}) {
	        return new ScanRow(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.file = source["file"];
	        this.path = source["path"];
	        this.frozenIntro = source["frozenIntro"];
	        this.firstChange = source["firstChange"];
	        this.freezeSec = source["freezeSec"];
	        this.error = source["error"];
	    }
	}
	export class TrimParams {
	    inputPath: string;
	    outputPath: string;
	    startTime: string;
	    endTime: string;
	    encoderMode: string;
	    replaceSource: boolean;
	
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
	        this.replaceSource = source["replaceSource"];
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


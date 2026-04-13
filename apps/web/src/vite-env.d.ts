/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_AMAP_WEB_KEY?: string;
  readonly VITE_AMAP_SECURITY_JS_CODE?: string;
  readonly VITE_AMAP_URI_SRC?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  AMap?: any;
  _AMapSecurityConfig?: {
    securityJsCode?: string;
  };
  __ARCADEGENT_AMAP_MOCK__?: {
    load?: () => Promise<any> | any;
  };
}

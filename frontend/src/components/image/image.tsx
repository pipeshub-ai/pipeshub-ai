import { forwardRef } from 'react';
import { LazyLoadImage } from 'react-lazy-load-image-component';

import Box from '@mui/material/Box';
import { styled } from '@mui/material/styles';

import { imageClasses } from './classes';

import type { ImageProps } from './types';

// ----------------------------------------------------------------------

const ImageWrapper = styled(Box)({
  overflow: 'hidden',
  position: 'relative',
  verticalAlign: 'bottom',
  display: 'inline-block',
  [`& .${imageClasses.wrapper}`]: {
    width: '100%',
    height: '100%',
    verticalAlign: 'bottom',
    backgroundSize: 'cover !important',
  },
});

const Overlay = styled('span')({
  top: 0,
  left: 0,
  zIndex: 1,
  width: '100%',
  height: '100%',
  position: 'absolute',
});

// ----------------------------------------------------------------------

export const Image = forwardRef<HTMLSpanElement, ImageProps>(
  (
    {
      alt,
      src,
      ratio,
      delayTime,
      threshold,
      beforeLoad,
      delayMethod,
      placeholder,
      wrapperProps,
      scrollPosition,
      effect = 'blur',
      visibleByDefault,
      wrapperClassName,
      disabledEffect = false,
      useIntersectionObserver,
      //
      sx,
      slotProps,
      className,
      ...other
    },
    ref
  ) => {
    const content = (
      <Box
        component={LazyLoadImage}
        alt={alt}
        src={src}
        delayTime={delayTime}
        threshold={threshold}
        beforeLoad={beforeLoad}
        delayMethod={delayMethod}
        placeholder={placeholder}
        wrapperProps={wrapperProps}
        scrollPosition={scrollPosition}
        visibleByDefault={visibleByDefault}
        effect={visibleByDefault || disabledEffect ? undefined : effect}
        useIntersectionObserver={useIntersectionObserver}
        wrapperClassName={wrapperClassName || imageClasses.wrapper}
        sx={{
          width: 1,
          height: 1,
          objectFit: 'cover',
          verticalAlign: 'bottom',
          aspectRatio: ratio,
        }}
      />
    );

    return (
      <ImageWrapper
        ref={ref}
        component="span"
        className={imageClasses.root.concat(className ? ` ${className}` : '')}
        sx={{ ...(!!ratio && { width: 1 }), ...sx }}
        {...other}
      >
        {slotProps?.overlay && <Overlay className={imageClasses.overlay} sx={slotProps?.overlay} />}

        {content}
      </ImageWrapper>
    );
  }
);

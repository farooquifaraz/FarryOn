//
//  QCSDKHelper.h
//  QCBandSDK
//
//  Created by steve on 2021/8/2.
//

#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@class QCSleepModel;

@interface QCSDKHelper : NSObject

+ (instancetype)shareInstance;

#pragma mark - 其他
- (void)convertOpusToPcm:(NSString *)inputPath
              outputPath:(NSString *)outputPath
                progress:(void (^_Nullable)(float progress))progress
              completion:(void (^_Nullable)(BOOL success))completion;             
@end

NS_ASSUME_NONNULL_END
